import OperatorDeviceLiveClient from './client';

export default async function OperatorDeviceLivePage({
    params
}: {
    params: Promise<{ id: string }>
}) {
    const { id } = await params;
    return <OperatorDeviceLiveClient id={id} />;
}
