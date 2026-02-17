import OperatorGreenhouseDetailClient from './client';

export default async function OperatorGreenhouseDetailPage({
    params
}: {
    params: Promise<{ id: string }>
}) {
    const { id } = await params;
    return <OperatorGreenhouseDetailClient id={id} />;
}
