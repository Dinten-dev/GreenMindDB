'use client';

import Link from 'next/link';
import ScrollReveal from '@/components/ScrollReveal';

/* ── Professional SVG Icons ──────────────── */
function IconDashboard() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1" />
            <rect x="14" y="3" width="7" height="7" rx="1" />
            <rect x="3" y="14" width="7" height="7" rx="1" />
            <rect x="14" y="14" width="7" height="7" rx="1" />
        </svg>
    );
}

function IconFleet() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="2" />
            <path d="M12 2v4" /><path d="M12 18v4" />
            <path d="M4.93 4.93l2.83 2.83" /><path d="M16.24 16.24l2.83 2.83" />
            <path d="M2 12h4" /><path d="M18 12h4" />
            <path d="M4.93 19.07l2.83-2.83" /><path d="M16.24 7.76l2.83-2.83" />
        </svg>
    );
}

function IconVitals() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 12h4l3-6 4 12 3-6h4" />
        </svg>
    );
}

function IconGreenhouses() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 21h18" />
            <path d="M5 21V7l7-4 7 4v14" />
            <path d="M9 21v-6h6v6" />
        </svg>
    );
}

function IconTeam() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="9" cy="7" r="3" />
            <path d="M3 21v-2a4 4 0 014-4h4a4 4 0 014 4v2" />
            <circle cx="17" cy="10" r="2" />
            <path d="M21 21v-1.5a3 3 0 00-3-3h-.5" />
        </svg>
    );
}

function IconShield() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            <path d="M9 12l2 2 4-4" />
        </svg>
    );
}

const features: { title: string; desc: string; icon: React.ReactNode }[] = [
    { title: 'Real-Time Dashboard', desc: 'Monitor all greenhouse conditions at a glance with clean data visualizations, live status indicators, and configurable alerts.', icon: <IconDashboard /> },
    { title: 'Hardware Fleet Management', desc: 'Deploy, monitor, and manage all connected diagnostic units from one place. Track uptime, health, and connectivity remotely.', icon: <IconFleet /> },
    { title: 'Environmental & Crop Intelligence', desc: 'Explore plant vitals and climate metrics across custom time ranges. Identify trends, anomalies, and early warning signs instantly.', icon: <IconVitals /> },
    { title: 'Multi-Greenhouse Operations', desc: 'Oversee multiple commercial sites from a single unified interface — each with independent monitoring, teams, and configurations.', icon: <IconGreenhouses /> },
    { title: 'Team Collaboration', desc: 'Invite your cultivation team with role-based access. Owners, managers, and staff — each with the right level of visibility and control.', icon: <IconTeam /> },
    { title: 'Enterprise-Grade Security', desc: 'End-to-end data protection, organizational access controls, and secure authentication are built in by default — not bolted on.', icon: <IconShield /> },
];

export default function ProductPage() {
    return (
        <div className="min-h-screen">
            <div className="pt-28 pb-24 px-6 max-w-[1280px] mx-auto">
                <ScrollReveal>
                    <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">Platform</p>
                    <h1 className="text-4xl md:text-6xl font-bold text-apple-gray-800 mb-6 tracking-tight">
                        One Platform.<br />Complete Greenhouse Intelligence.
                    </h1>
                </ScrollReveal>
                <ScrollReveal delay={200}>
                    <p className="text-xl text-apple-gray-400 max-w-2xl mb-20 leading-relaxed">
                        Everything you need to monitor plant health, optimize growing conditions, and make
                        faster, more precise cultivation decisions — in one unified interface.
                    </p>
                </ScrollReveal>

                <div className="flex hide-scrollbar overflow-x-auto snap-x snap-mandatory gap-4 md:grid md:grid-cols-2 md:gap-8 mb-24 pb-4 md:pb-0 -mx-6 px-6 md:mx-0 md:px-0">
                    {features.map((f, i) => (
                        <ScrollReveal key={f.title} delay={i * 100} className="w-[85vw] md:w-auto flex-shrink-0 snap-center">
                            <div className="card-hover bg-apple-gray-100 rounded-apple-lg p-8 h-full">
                                <div className="icon-container mb-5 text-gm-green-600">{f.icon}</div>
                                <h3 className="text-xl font-semibold text-apple-gray-800 mb-2">{f.title}</h3>
                                <p className="text-apple-gray-400 leading-relaxed">{f.desc}</p>
                            </div>
                        </ScrollReveal>
                    ))}
                </div>
            </div>
        </div>
    );
}
