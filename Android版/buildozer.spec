[app]
title = 课堂派资料下载
package.name = ktp_downloader
package.domain = com.ktp
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 1.0.0
requirements = python3,kivy==2.3.0,requests>=2.28.0,Pillow==8.4.0
orientation = portrait
fullscreen = 1
android.permissions = INTERNET
android.api = 34
android.minapi = 26
android.ndk = 25b
android.sdk = 34
android.arch = arm64-v8a
p4a.branch = develop
p4a.source_dir =
android.allow_backup = True
android.presplash_color = #3F51B5
android.splash_color = #FFFFFF
ios.kivy_ios_url = https://github.com/kivy/kivy-ios
ios.kivy_ios_branch = master
ios.ios_deploy_url = https://github.com/phonegap/ios-deploy
ios.ios_deploy_branch = 1.10.0

[buildozer]
log_level = 2
warn_on_root = 1
p4a.source_dir = /home/q295746337/ktp/.buildozer/android/platform/python-for-android
