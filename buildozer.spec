[app]

title = PDF Reader
package.name = PyRead
package.domain = org.pyread

source.dir = .
source.include_exts = py,png,jpg,kv,atlas
source.exclude_dirs = .github,bin,.buildozer,__pycache__

version = 1.0.0

# pymupdf has a p4a recipe (needs the develop branch below).
# androidstorage4kivy = Android file picker (no storage permission needed).
requirements = python3,kivy==2.3.0,pymupdf,pyjnius,android,androidstorage4kivy

orientation = portrait,landscape
fullscreen = 0

# The Storage Access Framework picker needs no runtime permissions.
android.permissions =

android.api = 34
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = True

# Required by androidstorage4kivy
android.enable_androidx = True
android.gradle_dependencies = androidx.core:core:1.6.0

# p4a.branch develop provides the pymupdf recipe
p4a.branch = develop

[buildozer]
log_level = 2
warn_on_root = 1
