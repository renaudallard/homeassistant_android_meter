plugins {
    id("com.android.application")
}

android {
    namespace = "it.allard.androidmeter"
    compileSdk = 37

    defaultConfig {
        applicationId = "it.allard.androidmeter"
        minSdk = 28
        targetSdk = 37
        versionCode = 13
        versionName = "0.1.12"
    }

    /* Release builds are signed when given a keystore. */
    val keystore = System.getenv("ANDROID_METER_KEYSTORE_FILE")
    if (keystore != null) {
        signingConfigs {
            create("release") {
                storeFile = file(keystore)
                storePassword = System.getenv("ANDROID_METER_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_METER_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_METER_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
            if (keystore != null) signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    /* F-Droid refuses the dependency list Google encrypts into the APK signing block. */
    dependenciesInfo {
        includeInApk = false
    }
}
