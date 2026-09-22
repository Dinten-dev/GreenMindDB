'use client';
import Link from 'next/link';
import { useLocale } from 'next-intl';
import StorageMonitor from './StorageMonitor';
export default function AdministrationPage() {
  const locale = useLocale();
  return (
    <div className="max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-800">Administration</h1>
        <p className="mt-1 text-sm text-gray-500">Serverspeicher und Benutzerzugänge verwalten.</p>
      </div>
      <StorageMonitor />
      <Link
        href={`/${locale}/app/administration/users`}
        className="glass-card block p-6 transition-colors hover:border-emerald-300"
      >
        <h2 className="text-lg font-semibold text-gray-800">Benutzerverwaltung →</h2>
        <p className="mt-2 text-sm text-gray-500">
          Benutzer anlegen, Firmen und Zonen zuweisen sowie Konten aktivieren oder deaktivieren.
        </p>
      </Link>
    </div>
  );
}
