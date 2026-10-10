import '../config.dart';

/// Accept only this deployment's opaque share links, never arbitrary URLs.
String? shareToken(String value) {
  final uri = Uri.tryParse(value);
  final origin = Uri.parse(AppConfig.apiBaseUrl);
  if (uri == null || uri.scheme != origin.scheme || uri.host != origin.host ||
      uri.port != origin.port || uri.userInfo.isNotEmpty ||
      uri.query.isNotEmpty || uri.fragment.isNotEmpty ||
      uri.pathSegments.length != 2 || uri.pathSegments.first != 's') {
    return null;
  }
  final token = uri.pathSegments.last;
  return RegExp(r'^[A-Za-z0-9_-]{43}$').hasMatch(token) ? token : null;
}
