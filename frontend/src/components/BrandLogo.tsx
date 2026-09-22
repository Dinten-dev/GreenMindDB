import Image from 'next/image';

export default function BrandLogo({ size = 40 }: { size?: number }) {
  return (
    <Image
      src="/greenmind-logo.png"
      alt="GreenMind"
      width={size}
      height={Math.round((size * 1184) / 1328)}
      className="shrink-0 object-contain"
      priority
    />
  );
}
