import re

with open("frontend/src/app/[locale]/app/zones/page.tsx", "r") as f:
    content = f.read()

# Replace <Link ... href={`/app/zones/${z.id}`} with <div
content = re.sub(
    r'<Link\s+key={z\.id}\s+href={`/app/zones/\$\{z\.id\}`}\s+className="block glass-card p-6 transition-all duration-300 hover:shadow-lg"\s+>',
    '<div key={z.id} className="block glass-card p-6 transition-all duration-300">',
    content
)

# Replace </Link> with </div> at the end of the loop body
content = re.sub(
    r'</Link>\s*\n\s*\)\)}',
    '</div>\n                    ))}',
    content
)

# Remove the hover arrow
content = re.sub(
    r'<span className="text-gray-400 text-lg">›</span>',
    '',
    content
)

# Ensure onClick for delete doesn't need to stop propagation as much, but we can leave it
content = content.replace("e.preventDefault(); e.stopPropagation(); setDeletingZoneId(z.id);", "setDeletingZoneId(z.id);")

with open("frontend/src/app/[locale]/app/zones/page.tsx", "w") as f:
    f.write(content)

