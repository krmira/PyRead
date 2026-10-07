[app]

title = PyRead
package.name = pyread
package.domain = org.pyread

source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,json

version = 0.1.0

requirements = python3,kivy,pymupdf

orientation = portrait
fullscreen = 0

android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE

android.api = 34
android.minapi = 23

android.archs = arm64-v8a,armeabi-v7a

source.exclude_exts = spec,pyc,pyo


[buildozer]

log_level = 2
warn_on_root = 1