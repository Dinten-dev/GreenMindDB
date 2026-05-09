import PlantDetailClient from './PlantDetailClient';

// Force dynamic rendering — PlantDetailClient uses useAuth context
// which is only available at runtime, not during static prerendering.
export const dynamic = 'force-dynamic';

export default function PlantDetailPage({ params }: { params: { id: string } }) {
    return <PlantDetailClient id={params.id} />;
}
