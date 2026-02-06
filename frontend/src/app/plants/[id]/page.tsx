import PlantDetailClient from './PlantDetailClient';

export default function PlantDetailPage({ params }: { params: { id: string } }) {
    return <PlantDetailClient id={params.id} />;
}

export async function generateStaticParams() {
    // Pre-generate pages for all 5 plants
    return [
        { id: '1' },
        { id: '2' },
        { id: '3' },
        { id: '4' },
        { id: '5' },
    ];
}
