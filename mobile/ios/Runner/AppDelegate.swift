import Flutter
import UIKit
import UserNotifications

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    if let registrar = engineBridge.pluginRegistry.registrar(forPlugin: "FindBackNotificationSettings") {
      let channel = FlutterMethodChannel(name: "findback/notifications", binaryMessenger: registrar.messenger())
      channel.setMethodCallHandler { call, result in
        if call.method == "openSettings", let url = URL(string: UIApplication.openSettingsURLString) {
          UIApplication.shared.open(url, options: [:]) { success in result(success ? nil : FlutterError(code: "settings", message: "Could not open Settings", details: nil)) }
        } else { result(FlutterMethodNotImplemented) }
      }
    }
    UNUserNotificationCenter.current().delegate = self
  }
}
