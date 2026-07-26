"""Signal filters for pose input.

THE PROBLEM WITH A FIXED LOW-PASS FILTER
Our exponential filter has one constant, and it is asked to do two incompatible
jobs. While the arm is HELD STILL we want heavy smoothing, because the deadband
must exceed the residual noise or the controller chases jitter. While the arm is
MOVING we want almost none, because lag here adds directly to a loop that is
already 150-300 ms slow. One alpha cannot be both, so it is set to a compromise
that is too noisy when still and too laggy when moving.

THE ONE EURO FILTER (Casiez, Roussel & Vogel, CHI 2012)
Make the cutoff a function of the signal's own speed. When the value is barely
changing, the cutoff drops and it smooths hard. When it starts moving, the
cutoff rises and the filter gets out of the way. Because "still" and "moving"
are exactly when we want the two different behaviours, this dissolves the
trade-off rather than splitting the difference.

Measured on a real 553-sample capture of a held posture at 27.6 Hz:

    filter                              elbow sd   step lag
    raw                                   2.51        0 ms
    median-5 + EMA 0.35   (the old one)   1.70      145 ms
    median-5 + 1-euro 0.25/0.005          1.14      145 ms

Same lag, a third less noise, which drops the required elbow deadband from
5.0 deg to 3.5 deg.

WHAT IT STILL CANNOT DO: reject outliers. A single badly mis-located landmark
is a large, fast change, which this filter reads as genuine motion and passes
through faster than a fixed filter would. So the median prefilter stays; the
two are complementary and the order matters (median first).
"""

import math


class OneEuroFilter:
    """Adaptive low-pass whose cutoff rises with the signal's speed.

    mincutoff: cutoff (Hz) as the signal approaches stationary. LOWER = smoother
               when still. This is the knob that sets your noise floor.
    beta:      how much the cutoff opens up per unit of speed. HIGHER = less lag
               while moving, more noise passed during movement.
    dcutoff:   cutoff for the speed estimate itself. 1.0 Hz is the paper's
               default and rarely needs changing.

    Tune mincutoff first with beta at 0, until a held posture is quiet enough.
    Then raise beta until movement stops feeling delayed.
    """

    def __init__(self, rate_hz, mincutoff=0.25, beta=0.005, dcutoff=1.0):
        self.rate = float(rate_hz)
        self.mincutoff = float(mincutoff)
        self.beta = float(beta)
        self.dcutoff = float(dcutoff)
        self._x = None            # last filtered value
        self._dx = 0.0            # filtered derivative

    def _alpha(self, cutoff):
        """Exponential-filter alpha equivalent to this cutoff at this rate."""
        cutoff = max(cutoff, 1e-6)
        tau = 1.0 / (2.0 * math.pi * cutoff)
        te = 1.0 / max(self.rate, 1e-6)
        return 1.0 / (1.0 + tau / te)

    def reset(self):
        self._x = None
        self._dx = 0.0

    def __call__(self, x, rate_hz=None):
        """Filter one sample. Pass rate_hz to track a varying sample rate."""
        if rate_hz:
            self.rate = float(rate_hz)
        if self._x is None:
            self._x = x
            return x

        # Speed estimate, itself low-passed - a raw derivative of a noisy
        # signal is noise, and would make the cutoff flap open at random.
        dx = (x - self._x) * self.rate
        a_d = self._alpha(self.dcutoff)
        self._dx = a_d * dx + (1.0 - a_d) * self._dx

        cutoff = self.mincutoff + self.beta * abs(self._dx)
        a = self._alpha(cutoff)
        self._x = a * x + (1.0 - a) * self._x
        return self._x


class RateGate:
    """Reject samples that imply a physically impossible joint velocity.

    WHY A MEDIAN IS NOT ENOUGH. A running median rejects an outlier only while
    the outlier is a minority of its window: median-5 handles one or two bad
    samples, but a BURST of six in a row simply becomes the median, and the bad
    values pass through as if they were signal. Measured on a real capture,
    shoulder abduction had bursts up to 6 consecutive mis-located samples - the
    exact case a median cannot touch, and the reason abduction filtered ~41%
    while the other joints filtered ~60%.

    A gate keyed on velocity catches those regardless of burst length, because
    it tests each sample against physics rather than against its neighbours.
    That same capture contained a 37 deg step between consecutive frames -
    about 1000 deg/s - while the subject was deliberately holding still. No
    shoulder does that; it is a mis-located landmark, and it can be rejected
    outright with no lag cost, unlike more filtering.

    IT LIMITS, IT DOES NOT HOLD. The obvious implementation - discard the
    sample and repeat the last good one until some retry count runs out - was
    measured and made things WORSE on the elbow (sd 1.42 -> 2.33), because a
    genuine fast level change gets held for the full retry window and then
    snaps, and that hold-then-snap is a larger deviation than the noise it
    replaced. Clamping the step instead means an isolated spike contributes at
    most one bounded excursion (which the median then removes), while a real
    move still arrives, just over two or three frames.

    Clamping also removes the need for an escape hatch. If the estimator
    switches which arm it is tracking, the true value jumps and a rejecting
    gate would freeze the pose indefinitely - far more dangerous than the noise
    it was added for. A limiter always converges.
    """

    def __init__(self, max_deg_per_s=400.0):
        # 400 deg/s is above any velocity FES can produce in a limb, and well
        # above anything seen while a subject holds a posture, so nothing real
        # is clipped. The same capture contained steps of ~1000 deg/s.
        self.max_deg_per_s = float(max_deg_per_s)
        self._last = None
        self.limited_total = 0

    def reset(self):
        self._last = None

    def __call__(self, x, rate_hz):
        if self._last is None or self.max_deg_per_s <= 0:
            self._last = x
            return x
        max_step = self.max_deg_per_s / max(rate_hz, 1e-6)
        delta = x - self._last
        if abs(delta) > max_step:
            self.limited_total += 1
            x = self._last + (max_step if delta > 0 else -max_step)
        self._last = x
        return x


class MedianWindow:
    """Odd-length running median. Rejects outliers outright.

    A single bad sample can never be the median of an odd window, so a
    mis-located landmark is discarded rather than smeared across the next
    several frames the way an exponential filter would smear it.
    """

    def __init__(self, n=5):
        self.n = n if n % 2 == 1 else n + 1     # must be odd
        self._buf = []

    def reset(self):
        self._buf = []

    def __call__(self, x):
        self._buf.append(x)
        if len(self._buf) > self.n:
            self._buf.pop(0)
        return sorted(self._buf)[len(self._buf) // 2]


class JointFilter:
    """The full chain for one joint angle: gate, median, adaptive low-pass.

    ORDER IS NOT ARBITRARY. Each stage handles what the next one cannot, and
    each must run before the stage it protects:

      1. RATE GATE   - limits physically impossible jumps, including long
                       bursts of them, which a median cannot.
      2. MEDIAN      - drops the isolated outliers that survive the gate
                       because they are small enough to be plausible.
      3. ONE-EURO    - smooths the continuous jitter that remains.

    The low-pass must come last: it reads a large fast change as genuine motion
    and opens its cutoff to follow it, so any outlier reaching it is passed
    through rather than rejected.
    """

    def __init__(self, rate_hz, median_n=5, mincutoff=0.25, beta=0.005,
                 mode="oneeuro", alpha=0.35, max_deg_per_s=400.0):
        self.mode = mode
        self.gate = RateGate(max_deg_per_s)
        self.median = MedianWindow(median_n)
        self.alpha = alpha
        self._ema = None
        self.oneeuro = OneEuroFilter(rate_hz, mincutoff, beta)

    def reset(self):
        self.gate.reset()
        self.median.reset()
        self.oneeuro.reset()
        self._ema = None

    def __call__(self, x, rate_hz=None):
        rate = rate_hz or self.oneeuro.rate
        med = self.median(self.gate(x, rate))
        if self.mode == "oneeuro":
            return self.oneeuro(med, rate_hz)
        # legacy fixed exponential, kept so the old behaviour is reproducible
        self._ema = med if self._ema is None else \
            self.alpha * med + (1.0 - self.alpha) * self._ema
        return self._ema
