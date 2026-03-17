'use client';

import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

export default function GlobalBackground() {
    const pathname = usePathname();
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    if (!mounted) return null;

    // Determine tone classes based on the active route
    let toneClasses = '';
    let neuralOpacity = 'opacity-0'; // Default hidden

    if (pathname === '/') {
        toneClasses = 'tone-home';
        neuralOpacity = 'opacity-30'; // subtle
    } else if (pathname === '/technology') {
        toneClasses = 'tone-technology';
        neuralOpacity = 'opacity-[0.85]'; // Strongest here
    } else if (pathname === '/product') {
        toneClasses = 'tone-product';
        neuralOpacity = 'opacity-50';
    } else if (pathname === '/about') {
        toneClasses = 'tone-about';
        neuralOpacity = 'opacity-20';
    } else if (pathname === '/contact' || pathname === '/early-access') {
        toneClasses = 'tone-contact';
        neuralOpacity = 'opacity-[0.15]';
    }

    return (
        <div className={`global-bg ${toneClasses}`}>
            {/* Organic Flow Blobs */}
            <div className="blob-first" />
            <div className="blob-second" />
            <div className="blob-third" />

            {/* Neural Signal Lines Overlay */}
            <div className={`neural-overlay ${neuralOpacity} transition-opacity duration-1000`}>
                <svg
                    className="w-full h-full"
                    viewBox="0 0 100 100"
                    preserveAspectRatio="none"
                    fill="none"
                    xmlns="http://www.w3.org/2000/svg"
                >
                    {/* Deep-Tech Signal Paths */}
                    <path
                        className="neural-path neural-path-1"
                        d="M -10,20 Q 20,40 50,30 T 110,60"
                        stroke="currentColor"
                        strokeWidth="0.1"
                        vectorEffect="non-scaling-stroke"
                    />
                    <path
                        className="neural-path neural-path-2"
                        d="M -10,70 Q 30,50 60,60 T 110,20"
                        stroke="currentColor"
                        strokeWidth="0.15"
                        vectorEffect="non-scaling-stroke"
                    />
                    <path
                        className="neural-path neural-path-3"
                        d="M 20,-10 Q 40,30 30,70 T 80,110"
                        stroke="currentColor"
                        strokeWidth="0.1"
                        vectorEffect="non-scaling-stroke"
                    />
                    <path
                        className="neural-path neural-path-4"
                        d="M 80,-10 Q 60,40 70,80 T 20,110"
                        stroke="currentColor"
                        strokeWidth="0.08"
                        vectorEffect="non-scaling-stroke"
                    />
                    {/* Intersection Nodes (Simulating Data Parsing) */}
                    <circle cx="50" cy="30" r="0.3" className="neural-node node-1" fill="currentColor" />
                    <circle cx="60" cy="60" r="0.4" className="neural-node node-2" fill="currentColor" />
                    <circle cx="30" cy="70" r="0.3" className="neural-node node-3" fill="currentColor" />
                    <circle cx="70" cy="80" r="0.5" className="neural-node node-4" fill="currentColor" />
                </svg>
            </div>
            
            {/* Soft Gradient Mask to ensure content readability */}
            <div className="global-bg-mask" />
        </div>
    );
}
