
[app]

title = PyRead
package.name = pyread
package.domain = org.krmira

source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas
source.exclude_dirs = .git,.github,.buildozer,bin,venv,.venv

version = 0.1.0

requirements = python3,kivy,pymupdf

orientation = portrait
fullscreen = 0

# Android configuration
android.api = 33
android.minapi = 23
android.ndk = 25b
android.ndk_api = 23

android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True

# Keep the app's data private
android.private_storage = True

# Build settings
p4a.bootstrap = sdl2
p4a.branch = master

[buildozer]

log_level = 2
warn_on_root = 1
