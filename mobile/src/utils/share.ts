export function extractUrlFromShareText(text: string): string | null {
  if (!text) return null;
  // prefer first https url
  const urlMatch = text.match(/https?:\/\/[^\s]+/);
  if (urlMatch) return urlMatch[0].replace(/[\u201c\u201d\)\]\.,]+$/, '');
  // if text itself is url-ish
  if (/^[\w.-]+\.[a-z]{2,}(\/\S*)?$/i.test(text.trim())) {
    return text.trim().startsWith('http') ? text.trim() : `https://${text.trim()}`;
  }
  return null;
}

export function previewFromText(text: string): string {
  return text.slice(0, 600);
}
