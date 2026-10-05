import 'dart:convert';

import 'package:flutter/services.dart';

Future<List<String>> loadCategories() async => List<String>.from(
    jsonDecode(await rootBundle.loadString('assets/categories.json')) as List);
