'use client';

import Link from 'next/link';
import ScrollReveal from '@/components/ScrollReveal';

export default function ContactPage() {
    return (
        <div className="min-h-screen">
            <div className="pt-28 pb-24 px-6 max-w-[1280px] mx-auto">
                <div className="max-w-2xl">
                    <ScrollReveal>
                        <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">Contact</p>
                        <h1 className="text-4xl md:text-6xl font-bold text-apple-gray-800 mb-6 tracking-tight">Get in Touch.</h1>
                    </ScrollReveal>
                    <ScrollReveal delay={200}>
                        <p className="text-xl text-apple-gray-400 mb-16 leading-relaxed">
                            Ready to elevate your cultivation operations? We&apos;d love to learn about your greenhouse and explore how GreenMind can help.
                        </p>
                    </ScrollReveal>

                    <ScrollReveal delay={350}>
                        <div className="bg-apple-gray-100 rounded-apple-lg p-10 border border-apple-gray-200/50">
                            <div className="flex flex-col md:flex-row gap-12">
                                <div className="flex-1">
                                    <h3 className="text-sm font-semibold text-apple-gray-500 uppercase tracking-wider mb-3">Email Us</h3>
                                    <a 
                                        href="mailto:traver.dinten@outlook.com" 
                                        className="text-2xl font-medium text-gm-green-600 hover:text-gm-green-500 transition-colors duration-200"
                                    >
                                        traver.dinten@outlook.com
                                    </a>
                                    <p className="mt-4 text-apple-gray-500 leading-relaxed">
                                        Drop us an email with your project details, and we will get back to you as soon as possible.
                                    </p>
                                </div>
                                
                                <div className="w-px bg-apple-gray-200 hidden md:block"></div>
                                
                                <div className="flex-1">
                                    <h3 className="text-sm font-semibold text-apple-gray-500 uppercase tracking-wider mb-3">Location</h3>
                                    <p className="text-lg text-apple-gray-800 leading-relaxed">
                                        FHNW Campus Muttenz<br />
                                        Basel, Switzerland
                                    </p>
                                </div>
                            </div>
                        </div>
                    </ScrollReveal>
                </div>
            </div>
        </div>
    );
}
