'use client';

import { useEffect, useRef, useState, ReactNode } from 'react';

interface ScrollRevealProps {
    children: ReactNode;
    variant?: 'fade-up' | 'fade-in' | 'scale-in';
    delay?: number;       // ms
    duration?: number;     // ms
    threshold?: number;    // 0–1
    once?: boolean;
    className?: string;
}

export default function ScrollReveal({
    children,
    variant = 'fade-up',
    delay = 0,
    duration = 700,
    threshold = 0.15,
    once = true,
    className = '',
}: ScrollRevealProps) {
    const ref = useRef<HTMLDivElement>(null);
    const [isVisible, setIsVisible] = useState(false);

    useEffect(() => {
        const node = ref.current;
        if (!node) return;

        const observer = new IntersectionObserver(
            ([entry]) => {
                if (entry.isIntersecting) {
                    setIsVisible(true);
                    if (once) observer.unobserve(node);
                } else if (!once) {
                    setIsVisible(false);
                }
            },
            { threshold, rootMargin: '0px 0px -40px 0px' }
        );

        observer.observe(node);
        return () => observer.disconnect();
    }, [threshold, once]);

    const baseStyles: React.CSSProperties = {
        transitionProperty: 'opacity, transform',
        transitionDuration: `${duration}ms`,
        transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
        transitionDelay: `${delay}ms`,
    };

    const hiddenStyles: Record<string, React.CSSProperties> = {
        'fade-up': { opacity: 0, transform: 'translateY(32px)' },
        'fade-in': { opacity: 0, transform: 'none' },
        'scale-in': { opacity: 0, transform: 'scale(0.95)' },
    };

    const visibleStyles: React.CSSProperties = {
        opacity: 1,
        transform: 'translateY(0) scale(1)',
    };

    return (
        <div
            ref={ref}
            className={className}
            style={{
                ...baseStyles,
                ...(isVisible ? visibleStyles : hiddenStyles[variant]),
            }}
        >
            {children}
        </div>
    );
}
