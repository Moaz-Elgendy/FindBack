const { withAndroidManifest, withInfoPlist } = require('@expo/config-plugins');

module.exports = function withFindBackShare(config) {
  config = withAndroidManifest(config, cfg => {
    const activity = cfg.modResults.manifest.application[0].activity?.[0];
    activity['intent-filter'] = activity['intent-filter'] || [];
    activity['intent-filter'].push({ action: [{ $: { 'android:name': 'android.intent.action.SEND' } }], category: [{ $: { 'android:name': 'android.intent.category.DEFAULT' } }], data: [{ $: { 'android:mimeType': 'text/plain' } }] });
    return cfg;
  });
  return withInfoPlist(config, cfg => {
    cfg.modResults.FindBackAppGroup = process.env.IOS_APP_GROUP || 'group.com.findback.app';
    return cfg;
  });
};
