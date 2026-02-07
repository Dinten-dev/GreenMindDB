"use client";

import React from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { format } from 'date-fns';

interface TimeseriesPoint {
    timestamp: string; // ISO string
    value: number;
}

interface LiveChartProps {
    data: TimeseriesPoint[];
    metricKey: string;
    unit: string;
    color?: string;
}

const LiveChart: React.FC<LiveChartProps> = ({ data, metricKey, unit, color = "#10b981" }) => {
    if (!data || data.length === 0) {
        return <div className="text-gray-400 p-4 text-center">No data available for this period</div>;
    }

    const formattedData = data.map(pt => ({
        ...pt,
        date: new Date(pt.timestamp).getTime(), // Recharts handles number x-axis better for time
    }));

    return (
        <div className="h-64 w-full bg-white/50 rounded-lg p-2">
            <ResponsiveContainer width="100%" height="100%">
                <LineChart data={formattedData}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e5e7eb" />
                    <XAxis
                        dataKey="date"
                        type="number"
                        domain={['dataMin', 'dataMax']}
                        tickFormatter={(unixTime) => format(unixTime, 'HH:mm')}
                        stroke="#9ca3af"
                        fontSize={12}
                    />
                    <YAxis
                        stroke="#9ca3af"
                        fontSize={12}
                        unit={unit}
                        width={40}
                    />
                    <Tooltip
                        labelFormatter={(label) => format(label, 'MMM dd HH:mm')}
                        formatter={(value: number) => [`${value.toFixed(1)} ${unit}`, metricKey]}
                        contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                    />
                    <Line
                        type="monotone"
                        dataKey="value"
                        stroke={color}
                        strokeWidth={2}
                        dot={false}
                        activeDot={{ r: 4 }}
                    />
                </LineChart>
            </ResponsiveContainer>
        </div>
    );
};

export default LiveChart;
