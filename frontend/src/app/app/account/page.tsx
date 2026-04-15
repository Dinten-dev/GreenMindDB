'use client';

import { useAuth } from '@/contexts/AuthContext';

export default function AccountPage() {
    const { user } = useAuth();

    if (!user) return null;

    return (
        <div className="space-y-6 max-w-2xl">
            <div>
                <h1 className="text-2xl font-bold text-apple-gray-800">Account</h1>
                <p className="text-sm text-apple-gray-400 mt-1">Your profile and settings</p>
            </div>

            <div className="bg-white rounded-apple-lg shadow-apple-card p-6 space-y-6">
                <div className="flex items-center gap-4">
                    <div className="w-14 h-14 rounded-full bg-gm-green-100 flex items-center justify-center">
                        <span className="text-2xl font-bold text-gm-green-600">
                            {(user.name || user.email).charAt(0).toUpperCase()}
                        </span>
                    </div>
                    <div>
                        <p className="text-lg font-semibold text-apple-gray-800">{user.name || 'No name set'}</p>
                        <p className="text-sm text-apple-gray-400">{user.email}</p>
                    </div>
                </div>

                <hr className="border-apple-gray-200" />

                <div className="grid grid-cols-2 gap-y-4 text-sm">
                    <span className="text-apple-gray-400">Role</span>
                    <span className="text-apple-gray-800 capitalize font-medium">{user.role}</span>

                    <span className="text-apple-gray-400">Organization</span>
                    <span className="text-apple-gray-800 font-medium">{user.organization_name || 'None'}</span>

                    <span className="text-apple-gray-400">Status</span>
                    <span className="text-apple-gray-800 font-medium flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full ${user.is_active ? 'bg-gm-green-500' : 'bg-red-500'}`} />
                        {user.is_active ? 'Active' : 'Disabled'}
                    </span>

                    <span className="text-apple-gray-400">User ID</span>
                    <span className="text-apple-gray-400 font-mono text-xs">{user.id}</span>
                </div>
            </div>
        </div>
    );
}
