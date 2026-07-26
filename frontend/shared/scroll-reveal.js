document.body.classList.add('reveal-ready');

const revealItems = document.querySelectorAll('.scroll-reveal');
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

if (reducedMotion.matches || !('IntersectionObserver' in window)) {
    revealItems.forEach((item) => item.classList.add('is-visible'));
} else {
    const revealObserver = new IntersectionObserver(
        (entries, observer) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) {
                    return;
                }
                entry.target.classList.add('is-visible');
                observer.unobserve(entry.target);
            });
        },
        {
            rootMargin: '0px 0px -12% 0px',
            threshold: 0.14,
        },
    );

    revealItems.forEach((item) => revealObserver.observe(item));
}
