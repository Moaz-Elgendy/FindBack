/// Text munging for the Save flow: a Share Sheet payload (or a clipboard blob
/// pasted by the user) is mostly prose around a URL, and we only want the URL.
library;

final RegExp _firstUrl = RegExp(r'https?://\S+');

/// Smart quotes, brackets and sentence punctuation that iOS/Android share
/// sheets glue onto the end of a URL. Mirrors the RN client's strip list.
final RegExp _trailingNoise = RegExp('[\u201C\u201D)\\].,]+\$');

/// Bare domains with no scheme, e.g. `example.com/recipes/1`.
///
/// Raw string, so the `$` here is the end anchor — `\$` would have matched a
/// literal dollar sign and made every bare domain unparseable.
final RegExp _bareDomain = RegExp(r'^[\w.-]+\.[a-z]{2,}(/\S*)?$', caseSensitive: false);

/// Returns the first URL inside free text, or null when there is none.
String? extractUrlFromShareText(String? text) {
  if (text == null || text.isEmpty) return null;
  final match = _firstUrl.firstMatch(text);
  if (match != null) return match.group(0)!.replaceAll(_trailingNoise, '');

  final trimmed = text.trim();
  if (_bareDomain.hasMatch(trimmed)) {
    return trimmed.startsWith('http') ? trimmed : 'https://$trimmed';
  }
  return null;
}

/// The preview the API stores while the real extraction is still pending.
String previewFromText(String text, {int maxLength = 600}) =>
    text.length <= maxLength ? text : text.substring(0, maxLength);

/// `www.`-stripped hostname; empty string when the URL is unusable.
String sourceDomain(String url) {
  final uri = Uri.tryParse(url);
  if (uri == null || uri.host.isEmpty) return '';
  final host = uri.host.toLowerCase();
  return host.startsWith('www.') ? host.substring(4) : host;
}

/// Title for the optimistic local row, before the server has extracted one:
/// explicit hint, else the head of the preview, else the URL itself.
String? deriveLocalTitle({String? titleHint, String? preview, String? fallback, int maxLength = 80}) {
  if (titleHint != null && titleHint.trim().isNotEmpty) return titleHint.trim();
  if (preview != null && preview.trim().isNotEmpty) {
    return preview.length <= maxLength ? preview : preview.substring(0, maxLength);
  }
  return fallback;
}
