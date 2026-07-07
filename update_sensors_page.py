import re

with open("frontend/src/app/[locale]/app/sensors/page.tsx", "r") as f:
    content = f.read()

# Add a check for URL parameters after sensors are loaded
patch = """    const refreshSensors = useCallback(() => {
        apiListSensors()
            .then((data) => {
                setSensors(data);
                if (typeof window !== 'undefined') {
                    const params = new URLSearchParams(window.location.search);
                    const sensorId = params.get('sensor');
                    if (sensorId && data.some(s => s.id === sensorId)) {
                        setSelectedSensor(sensorId);
                        // Clean up the URL
                        const newUrl = window.location.pathname;
                        window.history.replaceState({}, '', newUrl);
                    }
                }
            })
            .catch(console.error)
            .finally(() => setLoading(false));
    }, []);"""

content = re.sub(
    r"    const refreshSensors = useCallback\(\(\) => \{\n        apiListSensors\(\)\n            \.then\(setSensors\)\n            \.catch\(console\.error\)\n            \.finally\(\(\) => setLoading\(false\)\);\n    \}, \[\]\);",
    patch,
    content
)

with open("frontend/src/app/[locale]/app/sensors/page.tsx", "w") as f:
    f.write(content)

