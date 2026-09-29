import React, { useEffect, useRef } from 'react';
import { View, TextInput, StyleSheet, Text } from 'react-native';

const PLACEHOLDERS = [
  'Try: that chicken mushroom recipe...',
  'Try: that AWS tutorial about crashed apps...',
  'Try: AI tool for making presentations...',
  'Try: headphones noise canceling under 200...',
];

export function SearchBar({ value, onChange, autoFocus }: { value: string; onChange: (t: string) => void; autoFocus?: boolean }) {
  const [ph, setPh] = React.useState(PLACEHOLDERS[0]);
  const inputRef = useRef<TextInput>(null);

  useEffect(() => {
    let i = 0;
    const id = setInterval(() => { i = (i + 1) % PLACEHOLDERS.length; setPh(PLACEHOLDERS[i]); }, 3000);
    return () => clearInterval(id);
  }, []);

  return (
    <View style={styles.wrap}>
      <Text style={styles.icon}>🔍</Text>
      <TextInput
        ref={inputRef}
        value={value}
        onChangeText={onChange}
        placeholder={ph}
        placeholderTextColor="#999"
        style={styles.input}
        autoFocus={autoFocus}
        returnKeyType="search"
        autoCorrect={false}
        autoCapitalize="none"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#F2F2F7', borderRadius: 14, paddingHorizontal: 12, paddingVertical: 10 },
  icon: { fontSize: 16, marginRight: 8 },
  input: { flex: 1, fontSize: 16, color: '#111' },
});
