name: Build Android APK with Buildozer

on:
  push:
    branches: [ "main", "master" ]
  pull_request:
    branches: [ "main", "master" ]

jobs:
  build:
    runs-on: ubuntu-22.04

    steps:
    - name: Checkout repository
      uses: actions/checkout@v4

    - name: Set up JDK 17
      uses: actions/setup-java@v4
      with:
        distribution: 'temurin'
        java-version: '17'

    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: '3.10'

    - name: Install System Dependencies
      run: |
        sudo apt-get update
        sudo apt-get install -y \
          git zip unzip python3-pip autoconf libtool pkg-config \
          zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 \
          cmake libffi-dev libssl-dev ccache Android-sdk-build-tools

    - name: Install Buildozer and Cython
      run: |
        pip install --upgrade pip setuptools
        pip install buildozer cython==0.29.33

    - name: Pre-accept Android SDK Licenses & Prepare Aidl
      run: |
        # Pre-create Android SDK directory layout so buildozer doesn't throw AIDL errors
        mkdir -p $HOME/.buildozer/android/platform/android-sdk/build-tools/34.0.0
        mkdir -p $HOME/.buildozer/android/platform/android-sdk/licenses
        
        # Auto-accept all Android SDK licenses
        echo -e "\n24333f8a637b3701d543d11247ef301da736e07b\n893305615feb5711112f16c2968a163750e1639d\n791244e692ef40920093228c16d7e6c5f5941036" > $HOME/.buildozer/android/platform/android-sdk/licenses/android-sdk-license

    - name: Build Android APK
      run: |
        yes | buildozer android debug

    - name: Upload APK Artifact
      uses: actions/upload-artifact@v4
      with:
        name: PyRead-Python-APK
        path: bin/*.apk
