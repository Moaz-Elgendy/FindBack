import React,{useEffect,useState,useCallback} from 'react';
import {View,Text,StyleSheet,FlatList,Pressable,ActivityIndicator,Linking,Alert} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';
import * as Clipboard from 'expo-clipboard';
import NetInfo from '@react-native-community/netinfo';
import {SearchBar} from '../components/SearchBar';
import {ResultCard} from '../components/ResultCard';
import {useSearch} from '../hooks/useSearch';
import {queueSave,getDb,upsertRemoteItems} from '../db';
import {flushQueue,startSyncListeners} from '../services/sync';
import {getItem,deleteItem,listItems} from '../services/api';
import {extractUrlFromShareText} from '../utils/share';
const CHIPS=['All','Recipes','Tutorials','Products','Tools'];
export function HomeScreen(){
  const {query,setQuery,results,loading,offline,tookMs,doSearch}=useSearch();
  const [chip,setChip]=useState('All');
  const [queued,setQueued]=useState(0);
  const [recent,setRecent]=useState<any[]>([]);
  const [online,setOnline]=useState(true);
  const refreshRecent=useCallback(async()=>{
    try{
      const net=await NetInfo.fetch();
      if(net.isConnected){
        const res:any=await listItems(10);
        const items=res.items??res??[]; setRecent(items);
        if(items.length) await upsertRemoteItems(items);
      } else {
        const db=await getDb();
        setRecent(await db.getAllAsync('SELECT * FROM items ORDER BY created_at DESC LIMIT 10'));
      }
    }catch{}
  },[]);
  useEffect(()=>{
    const u1=NetInfo.addEventListener(s=>setOnline(!!s.isConnected));
    const u2=startSyncListeners(()=>{setQueued(0);refreshRecent();});
    refreshRecent();
    const id=setInterval(async()=>{
      const db=await getDb();
      const r:any=await db.getFirstAsync("SELECT COUNT(*) as c FROM sync_queue WHERE status='pending'");
      setQueued(r?.c??0);
    },2000);
    return()=>{u1();u2();clearInterval(id);};
  },[refreshRecent]);
  const onSave=async()=>{
    const text=await Clipboard.getStringAsync();
    if(!text){Alert.alert('Clipboard empty','Copy a link first.');return;}
    const url=extractUrlFromShareText(text)??text.trim();
    await queueSave(url,text.slice(0,600));
    Alert.alert('Saved',!online?'Saved offline - will sync when online':'Remembering it for you...');
    await flushQueue(); refreshRecent();
  };
  const onChip=(c:string)=>{setChip(c); if(query.trim()) doSearch(query,c);};
  const onPress=async(id:string)=>{
    try{ const d:any=await getItem(id).catch(()=>null); if(d?.url) Linking.openURL(d.url); else { const l=results.find(r=>r.id===id); if(l) Alert.alert(l.title??'Memory',l.summary??''); } }catch{}
  };
  const onDel=async(id:string)=>{
    try{ if(!id.startsWith('local-')) await deleteItem(id); const db=await getDb(); await db.runAsync('DELETE FROM items WHERE id=?',[id]); refreshRecent(); if(query.trim()) doSearch(query,chip); }catch{}
  };
  const show=query.trim().length>0;
  return (
    <SafeAreaView style={s.root} edges={['top']}>
      <View style={s.header}><Text style={s.brand}>FindBack</Text><Text style={s.sub}>You dont need to remember where you saved it.</Text></View>
      <View style={s.wrap}>
        <SearchBar value={query} onChange={t=>{setQuery(t); if(t.trim()) doSearch(t,chip);}} autoFocus />
        <View style={s.row}><Text style={[s.dot,{color:online?'#0a0':'#e67e22'}]}> {online?'Online':'Offline - queued '+queued}</Text>{tookMs!=null?<Text style={s.took}>{tookMs}ms</Text>:null}{offline?<Text style={s.off}>Offline - lite results</Text>:null}</View>
        <View style={s.chips}>{CHIPS.map(c=>(<Pressable key={c} onPress={()=>onChip(c)} style={[s.chip,chip===c&&s.on]}><Text style={[s.chipT,chip===c&&s.chipTOn]}>{c}</Text></Pressable>))}</View>
      </View>
      {loading?<ActivityIndicator style={{marginTop:12}}/>:null}
      {!show?(
        <View style={s.empty}>
          <Text style={s.eTitle}>Share anything to FindBack to start</Text>
          <Text style={s.eSub}>Save from YouTube / TikTok / articles / recipes - find later with vague memory like that chicken mushroom recipe.</Text>
          <View style={s.exs}><Text style={s.ex}>That video about an AI tool for making presentations</Text><Text style={s.ex}>The chicken recipe with cream and mushrooms</Text><Text style={s.ex}>That AWS tutorial about fixing crashed applications</Text></View>
          <Pressable style={s.btn} onPress={onSave}><Text style={s.btnT}>Save from Clipboard</Text></Pressable>
          {recent.length>0?<View style={{width:'100%',marginTop:18}}><Text style={s.rT}>Recent memories</Text>{recent.slice(0,3).map((r:any)=>(<Pressable key={r.id} onPress={()=>r.url&&Linking.openURL(r.url)} style={s.rCard}><Text numberOfLines={1} style={{fontWeight:'600'}}>{r.title_clean??r.title??r.url}</Text><Text numberOfLines={1} style={{color:'#666',fontSize:12}}>{r.summary??''}</Text></Pressable>))}</View>:null}
        </View>
      ):(
        <FlatList data={results} keyExtractor={it=>it.id} contentContainerStyle={{padding:16,paddingBottom:32}} renderItem={({item})=>(
          <View><ResultCard item={item} onPress={()=>onPress(item.id)} /><Pressable onPress={()=>onDel(item.id)}><Text style={s.del}>Remove</Text></Pressable></View>
        )} ListEmptyComponent={!loading?<Text style={{textAlign:'center',color:'#999',marginTop:24}}>No exact match - try fewer words or check Recent.</Text>:null} />
      )}
      <Pressable style={s.fab} onPress={onSave}><Text style={s.fabT}>+ Save</Text></Pressable>
    </SafeAreaView>
  );
}
const s=StyleSheet.create({
  root:{flex:1,backgroundColor:'#FAFAFA'},
  header:{paddingHorizontal:16,paddingTop:8,paddingBottom:6},
  brand:{fontSize:22,fontWeight:'800'}, sub:{fontSize:12,color:'#666',marginTop:2},
  wrap:{paddingHorizontal:16,paddingTop:10,backgroundColor:'#fff',borderBottomWidth:1,borderBottomColor:'#EEE',paddingBottom:10},
  row:{flexDirection:'row',alignItems:'center',gap:8,marginTop:6}, dot:{fontSize:11}, took:{fontSize:11,color:'#999'}, off:{fontSize:11,color:'#e67e22',marginLeft:6},
  chips:{flexDirection:'row',flexWrap:'wrap',gap:6,marginTop:8}, chip:{paddingHorizontal:10,paddingVertical:6,borderRadius:20,backgroundColor:'#F2F2F7'}, on:{backgroundColor:'#111'}, chipT:{fontSize:12,color:'#333'}, chipTOn:{color:'#fff'},
  empty:{flex:1,alignItems:'center',padding:20,paddingTop:28}, eTitle:{fontSize:16,fontWeight:'700'}, eSub:{textAlign:'center',color:'#666',marginTop:6,fontSize:13}, exs:{marginTop:14,gap:6,width:'100%'}, ex:{fontSize:12,color:'#888',fontStyle:'italic',backgroundColor:'#fff',padding:8,borderRadius:8,borderWidth:1,borderColor:'#EEE'},
  btn:{marginTop:16,backgroundColor:'#111',paddingHorizontal:18,paddingVertical:10,borderRadius:10}, btnT:{color:'#fff',fontWeight:'600'},
  rT:{fontSize:12,fontWeight:'600',color:'#666',marginBottom:6}, rCard:{backgroundColor:'#fff',padding:10,borderRadius:10,borderWidth:1,borderColor:'#EEE',marginBottom:6},
  del:{fontSize:11,color:'#c00',textAlign:'right',marginBottom:10,marginTop:-4},
  fab:{position:'absolute',right:16,bottom:24,backgroundColor:'#111',paddingHorizontal:18,paddingVertical:12,borderRadius:24,elevation:4}, fabT:{color:'#fff',fontWeight:'700'},
});
