import React from 'react';
import { Alert, Linking, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import * as KeepAwake from 'expo-keep-awake';

export function DetailScreen({ item, visible, onClose, onDelete }: { item: any; visible: boolean; onClose: () => void; onDelete: () => void }) {
  const [cookMode, setCookMode] = React.useState(false);
  React.useEffect(() => {
    if (cookMode) KeepAwake.activateKeepAwakeAsync('cook-mode');
    else KeepAwake.deactivateKeepAwake('cook-mode');
    return () => { KeepAwake.deactivateKeepAwake('cook-mode'); };
  }, [cookMode]);
  if (!item) return null;
  const ingredients = item.entities?.ingredients ?? [];
  return <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
    <ScrollView contentContainerStyle={s.root}>
      <Pressable onPress={onClose}><Text style={s.close}>Close</Text></Pressable>
      <Text style={s.title}>{item.title_clean ?? item.title ?? 'Saved Memory'}</Text>
      <Text style={s.summary}>{item.summary ?? 'Open the original to view this memory.'}</Text>
      {(item.key_points ?? []).map((point: string) => <Text key={point} style={s.point}>• {point}</Text>)}
      {ingredients.length > 0 && <><Text style={s.heading}>Ingredients</Text>{ingredients.map((x: string) => <Text key={x}>• {x}</Text>)}</>}
      <View style={s.actions}>
        <Pressable style={s.primary} onPress={() => Linking.openURL(item.url)}><Text style={s.primaryText}>Open Original</Text></Pressable>
        <Pressable style={s.secondary} onPress={() => Clipboard.setStringAsync(item.summary ?? '')}><Text>Copy Summary</Text></Pressable>
        {item.category === 'recipe' && <Pressable style={s.secondary} onPress={() => setCookMode(v => !v)}><Text>{cookMode ? 'Exit Cook Mode' : 'Cook Mode'}</Text></Pressable>}
        <Pressable style={s.danger} onPress={() => Alert.alert('Delete memory?', 'This cannot be undone.', [{ text: 'Cancel' }, { text: 'Delete', style: 'destructive', onPress: onDelete }])}><Text style={{ color: '#b00020' }}>Delete</Text></Pressable>
      </View>
    </ScrollView>
  </Modal>;
}

const s = StyleSheet.create({ root: { padding: 24, gap: 12 }, close: { color: '#1769aa' }, title: { fontSize: 24, fontWeight: '800' }, summary: { fontSize: 16, lineHeight: 24 }, point: { fontSize: 15 }, heading: { fontSize: 18, fontWeight: '700', marginTop: 8 }, actions: { gap: 10, marginTop: 16 }, primary: { backgroundColor: '#111', padding: 14, borderRadius: 10, alignItems: 'center' }, primaryText: { color: '#fff', fontWeight: '700' }, secondary: { backgroundColor: '#eee', padding: 14, borderRadius: 10, alignItems: 'center' }, danger: { padding: 14, alignItems: 'center' } });
