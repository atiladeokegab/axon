"""Discrete PI controller with anti-windup and a deadband.

WHY PI AND NOT PID:
  * The I term is non-negotiable. Holding a joint against gravity needs a
    STEADY output at zero error - a pure-P controller can only produce that by
    sitting at a permanent error (the arm droops). The integrator also absorbs
    muscle fatigue, which steadily lowers the plant gain during a session.
  * The D term is actively harmful here: the angle comes from vision and is
    noisy (differentiating noise -> chatter on a relay), the ~150-300 ms dead
    time makes the derivative estimate stale, and the plant (muscle + limb) is
    already heavily damped, so there is no oscillation for D to tame.

Anti-windup matters because the output saturates constantly (duty is clamped
to 0..DUTY_MAX). Without it the integrator keeps accumulating against the
clamp and the controller overshoots badly when the error finally reverses.
"""


class PIController:
    """PI with anti-windup.

    NOTE ON `i_limit`: it bounds the integral's CONTRIBUTION TO THE OUTPUT
    (i.e. ki * integral), in duty units - not the raw accumulator. This is the
    tunable that matters: to hold a joint against gravity the integral alone
    must be able to supply the steady holding duty, so i_limit must be >= the
    duty needed to hold the heaviest pose (typically near duty_max). Bounding
    the raw accumulator instead makes the limit depend on ki, which is a
    classic way to silently cripple the controller.
    """

    def __init__(self, kp, ki, deadband=0.0, i_limit=0.7, out_min=0.0,
                 out_max=1.0):
        self.kp = kp
        self.ki = ki
        self.deadband = deadband
        # Convert the duty-unit limit into an accumulator bound.
        self.i_limit = (i_limit / ki) if ki > 0 else 0.0
        self.out_min = out_min
        self.out_max = out_max
        self._integral = 0.0
        self._last_error = 0.0

    def reset(self):
        self._integral = 0.0
        self._last_error = 0.0

    def update(self, error, dt):
        """Return a signed control effort for the given error.

        Sign convention: positive effort drives the joint angle UP (agonist),
        negative drives it DOWN (antagonist). The caller routes the sign to the
        correct muscle channel.
        """
        self._last_error = error

        # ---- inside the deadband: HOLD, do not collapse --------------------
        # We freeze the integrator (stop adapting) but keep supplying the
        # holding duty it has already learned.
        #
        # WHY NOT RETURN 0: holding a limb against gravity needs a CONSTANT
        # muscle torque. Returning 0 here removes that torque, the arm sags out
        # of the deadband, the controller fires again, the arm rises, output
        # drops to 0 - a limit cycle. Measured on the simulated plant that was
        # ~26 relay transitions per second at a supposedly steady setpoint,
        # which is audible, wears mechanical contacts, and looks broken.
        # Holding the integral instead gives 0 transitions and 0 ripple.
        #
        # The output is still bounded by out_max, and the caller (and the
        # firmware) still clamp on top of this.
        #
        # NOTE: this means stimulation CONTINUES while holding a pose, which is
        # physically unavoidable - a muscle must stay contracted to hold a limb
        # up. The firmware's MAX_BURST_MS / COOLDOWN_MS therefore now applies to
        # sustained holds, which is the intended safety behaviour: the arm is
        # allowed to sag during the forced rest rather than fatiguing the muscle.
        if abs(error) < self.deadband:
            hold = self.ki * self._integral
            if hold > self.out_max:
                return self.out_max
            if hold < -self.out_max:
                return -self.out_max
            return hold

        candidate_i = self._integral + error * dt
        raw = self.kp * error + self.ki * candidate_i

        # Conditional integration: only accumulate if we are not saturated, or
        # if the error would pull us back out of saturation.
        saturated_high = raw >= self.out_max
        saturated_low = raw <= -self.out_max
        if not (saturated_high and error > 0) and not (saturated_low and error < 0):
            self._integral = candidate_i

        # Hard backstop on the integrator regardless.
        if self._integral > self.i_limit:
            self._integral = self.i_limit
        elif self._integral < -self.i_limit:
            self._integral = -self.i_limit

        out = self.kp * error + self.ki * self._integral
        if out > self.out_max:
            out = self.out_max
        elif out < -self.out_max:
            out = -self.out_max
        return out

    def state(self):
        return {"integral": round(self._integral, 4),
                "last_error": round(self._last_error, 3)}
