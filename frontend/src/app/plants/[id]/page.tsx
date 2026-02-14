import { use } from 'react';
import PlantDetailClient from './PlantDetailClient';

type PageProps = {
    params: Promise<{ id: string }>;
};

export default function PlantDetailPage({ params }: PageProps) {
    const { id } = use(params);
    return <PlantDetailClient id={id} />;
}

export async function generateStaticParams() {
    // Pre-generate pages for all 4 plants
    return [
        { id: '1' },
        { id: '2' },
        { id: '3' },
        { id: '4' },
    ];
}

