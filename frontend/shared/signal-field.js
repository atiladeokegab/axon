const globalSignalField = document.getElementById('signalField');

if (globalSignalField) {
    let signalPointerFrame = null;
    let signalPointerX = 0;
    let signalPointerY = 0;
    let signalLightX = 50;
    let signalLightY = 50;
    let signalParallaxEnabled = false;

    const finePointerQuery = window.matchMedia('(pointer: fine)');
    const backgroundReducedMotionQuery = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
    );

    function renderSignalParallax() {
        globalSignalField.style.setProperty('--signal-shift-x', `${signalPointerX}px`);
        globalSignalField.style.setProperty('--signal-shift-y', `${signalPointerY}px`);
        globalSignalField.style.setProperty('--signal-light-x', `${signalLightX}%`);
        globalSignalField.style.setProperty('--signal-light-y', `${signalLightY}%`);
        signalPointerFrame = null;
    }

    function queueSignalParallax(x, y, lightX = 50, lightY = 50) {
        signalPointerX = x;
        signalPointerY = y;
        signalLightX = lightX;
        signalLightY = lightY;
        if (signalPointerFrame === null) {
            signalPointerFrame = requestAnimationFrame(renderSignalParallax);
        }
    }

    function handleSignalPointer(event) {
        const normalizedX = (event.clientX / window.innerWidth) * 2 - 1;
        const normalizedY = (event.clientY / window.innerHeight) * 2 - 1;
        const lightX = (normalizedX + 1) * 50;
        const lightY = (normalizedY + 1) * 50;
        queueSignalParallax(normalizedX * 24, normalizedY * 24, lightX, lightY);
    }

    function recenterSignalField() {
        queueSignalParallax(0, 0);
    }

    function syncSignalParallax() {
        const shouldEnable = (
            finePointerQuery.matches
            && !backgroundReducedMotionQuery.matches
        );
        if (shouldEnable === signalParallaxEnabled) {
            return;
        }

        signalParallaxEnabled = shouldEnable;
        if (shouldEnable) {
            window.addEventListener('pointermove', handleSignalPointer, { passive: true });
            document.documentElement.addEventListener('pointerleave', recenterSignalField);
            return;
        }

        window.removeEventListener('pointermove', handleSignalPointer);
        document.documentElement.removeEventListener('pointerleave', recenterSignalField);
        recenterSignalField();
    }

    finePointerQuery.addEventListener('change', syncSignalParallax);
    backgroundReducedMotionQuery.addEventListener('change', syncSignalParallax);
    syncSignalParallax();
}
