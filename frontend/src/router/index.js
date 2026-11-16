export function route(){const parts=location.hash.replace(/^#\//,'').split('/');const page=parts[0]||'overview';const id=parts[1];return {page,id};}
