import React from 'react';
import { View, Text, Image, StyleSheet, Pressable, Linking } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import type { SearchResult } from '../hooks/useSearch';

export function ResultCard({ item, onPress }: { item: SearchResult; onPress?: () => void }) {
  return (
    <Pressable onPress={onPress ?? (() => item.id && Linking.openURL(`https://`))} style={styles.card}>
      <View style={styles.row}>
        {item.thumbnail ? (
          <Image source={{ uri: item.thumbnail }} style={styles.thumb} />
        ) : (
          <View style={[styles.thumb, styles.thumbPh]}><Text style={{ fontSize: 18 }}>🔖</Text></View>
        )}
        <View style={{ flex: 1 }}>
          <Text numberOfLines={1} style={styles.title}>{item.title || 'Saved Memory'}</Text>
          <Text numberOfLines={1} style={styles.summary}>{item.summary || 'Open to view original'}</Text>
          <View style={styles.metaRow}>
            <Text style={styles.chip}>{item.category ?? 'memory'}</Text>
            {(item.tags ?? []).slice(0, 2).map(t => (
              <Text key={t} style={[styles.chip, styles.tagChip]}>{t}</Text>
            ))}
            {item.source_domain ? <Text style={styles.domain}>{item.source_domain}</Text> : null}
          </View>
          {item.match_reason ? <Text style={styles.match}>{item.match_reason}</Text> : null}
        </View>
      </View>
      <View style={styles.actions}>
        <Pressable style={styles.primaryBtn} onPress={() => onPress?.()}><Text style={styles.primaryTxt}>Open</Text></Pressable>
        <Pressable style={styles.secondaryBtn} onPress={() => Clipboard.setStringAsync(item.summary || item.title || '')}>
          <Text style={styles.secondaryTxt}>Copy</Text>
        </Pressable>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: '#fff', borderRadius: 14, padding: 12, marginBottom: 10, borderWidth: 1, borderColor: '#EEE', shadowColor: '#000', shadowOpacity: 0.04, shadowRadius: 8, elevation: 1 },
  row: { flexDirection: 'row', gap: 10 },
  thumb: { width: 56, height: 56, borderRadius: 10, backgroundColor: '#F2F2F7' },
  thumbPh: { alignItems: 'center', justifyContent: 'center' },
  title: { fontSize: 14, fontWeight: '600', color: '#111' },
  summary: { fontSize: 12, color: '#666', marginTop: 2 },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 6, flexWrap: 'wrap' },
  chip: { fontSize: 10, backgroundColor: '#F2F2F7', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 6, color: '#555', overflow: 'hidden' },
  tagChip: { backgroundColor: '#E8F0FE', color: '#1a73e8' },
  domain: { fontSize: 10, color: '#999' },
  match: { fontSize: 10, color: '#888', marginTop: 4, fontStyle: 'italic' },
  actions: { flexDirection: 'row', gap: 8, marginTop: 10 },
  primaryBtn: { backgroundColor: '#111', paddingHorizontal: 14, paddingVertical: 6, borderRadius: 8 },
  primaryTxt: { color: '#fff', fontSize: 12, fontWeight: '600' },
  secondaryBtn: { backgroundColor: '#F2F2F7', paddingHorizontal: 14, paddingVertical: 6, borderRadius: 8 },
  secondaryTxt: { color: '#333', fontSize: 12, fontWeight: '500' },
});
