import AdminGreenhouseDetailClient from './client';

export default async function AdminGreenhouseDetailPage({
    params
}: {
    params: Promise<{ id: string }>
}) {
    const { id } = await params;
    return <AdminGreenhouseDetailClient id={id} />;
}
