#!/usr/bin/env python3
"""
Deterministic Android ARM64 (linux-bionic-arm64) port & repair script for
LavaGang/MelonLoader v0.7.3 Open-Beta, using LemonLoader 0.7.0 Android NativeAOT
primitives and SamboyCoding/Cpp2IL in-process dumping.

Produces a single-library NativeAOT bootstrap (libmain.so) + MelonLoader v0.7.3
(.NET 8) managed assemblies + .NET 8.0.6 fixed-GC CoreCLR bundle.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path


def write_utf8(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Expected pattern not found for {label}:\n{old[:200]}")
    return text.replace(old, new, 1)


def copy_lemon070_android_files(root: Path, lemon_070: Path) -> None:
    # 1. JNI + APKAssetManager
    jni_src = lemon_070 / "MelonLoader/JNI"
    jni_dst = root / "MelonLoader/JNI"
    if jni_dst.exists():
        shutil.rmtree(jni_dst)
    shutil.copytree(jni_src, jni_dst)

    shutil.copy2(
        lemon_070 / "MelonLoader/Utils/APKAssetManager.cs",
        root / "MelonLoader/Utils/APKAssetManager.cs",
    )

    patch_lemon070_jni_and_asset_manager(root)

    # 2. Native static deps for linux-bionic-arm64 (libdobby.a, libplthook.a)
    deps_src = lemon_070 / "MelonLoader.Bootstrap/Deps/linux-bionic-arm64"
    deps_dst = root / "MelonLoader.Bootstrap/Deps/linux-bionic-arm64"
    deps_dst.mkdir(parents=True, exist_ok=True)
    for lib in ("libdobby.a", "libplthook.a"):
        shutil.copy2(deps_src / lib, deps_dst / lib)

    # 3. ClrMonoLib.cs
    shutil.copy2(
        lemon_070 / "MelonLoader.Bootstrap/RuntimeHandlers/Il2Cpp/ClrMonoLib.cs",
        root / "MelonLoader.Bootstrap/RuntimeHandlers/Il2Cpp/ClrMonoLib.cs",
    )

    print("[copied] LemonLoader 0.7.0 JNI, APKAssetManager, linux-bionic-arm64 deps, and ClrMonoLib")


def patch_lemon070_jni_and_asset_manager(root: Path) -> None:
    # Fix 1: JObject.cs must NEVER call DeleteLocalRef from the GC Finalizer thread (!disposing),
    # because ART JNI aborts if another thread deletes a thread-local reference.
    jobj_path = root / "MelonLoader/JNI/JObject.cs"
    jobj_text = jobj_path.read_text(encoding="utf-8")
    jobj_text = replace_once(
        jobj_text,
        "            case JNI.ReferenceType.Local:\n"
        "                JNI.DeleteLocalRef(this);\n"
        "                break;",
        "            case JNI.ReferenceType.Local:\n"
        "                if (disposing)\n"
        "                    JNI.DeleteLocalRef(this);\n"
        "                break;",
        "JObject.cs guard DeleteLocalRef to disposing thread",
    )
    write_utf8(jobj_path, jobj_text)

    # Fix 2: JNI.FindClass must lock ClassCache, never cache failed lookups (IntPtr.Zero),
    # and return a fresh LocalRef instead of the cached GlobalRef so `using JClass` never
    # deletes the cached GlobalRef out from under ClassCache!
    jni_path = root / "MelonLoader/JNI/JNI.cs"
    jni_text = jni_path.read_text(encoding="utf-8")
    jni_text = replace_once(
        jni_text,
        "    public static void Initialize(IntPtr vmPtr)\n"
        "    {\n"
        "        if (vmPtr == IntPtr.Zero)\n"
        "            vmPtr = lastVmPtr;\n\n"
        "        lastVmPtr = vmPtr;\n\n"
        "        unsafe\n"
        "        {\n"
        "            VM = (JavaVM*)vmPtr;\n\n"
        "            IntPtr envPtr = IntPtr.Zero;\n"
        "            Result res = VM->Functions->GetEnv(VM, out envPtr, (int)Version.V1_6);\n\n"
        "            if (res != Result.Detached && envPtr != IntPtr.Zero)\n"
        "            {\n"
        "                env = (JNIEnv*)envPtr;\n"
        "                return;\n"
        "            }\n\n"
        "            IntPtr refPtr = IntPtr.Zero;\n"
        "            res = VM->Functions->AttachCurrentThread(VM, out JNIEnv* localEnv, ref refPtr);\n\n"
        "            env = localEnv;\n"
        "        }\n"
        "    }",
        "    public static void Initialize(IntPtr vmPtr)\n"
        "    {\n"
        "        if (vmPtr == IntPtr.Zero)\n"
        "            vmPtr = lastVmPtr;\n"
        "        if (vmPtr == IntPtr.Zero)\n"
        "            return;\n\n"
        "        lastVmPtr = vmPtr;\n\n"
        "        unsafe\n"
        "        {\n"
        "            VM = (JavaVM*)vmPtr;\n"
        "            if (VM == null || VM->Functions == null)\n"
        "                return;\n\n"
        "            IntPtr envPtr = IntPtr.Zero;\n"
        "            Result res = VM->Functions->GetEnv(VM, out envPtr, (int)Version.V1_6);\n\n"
        "            if (res != Result.Detached && envPtr != IntPtr.Zero)\n"
        "            {\n"
        "                env = (JNIEnv*)envPtr;\n"
        "                return;\n"
        "            }\n\n"
        "            IntPtr refPtr = IntPtr.Zero;\n"
        "            res = VM->Functions->AttachCurrentThread(VM, out JNIEnv* localEnv, ref refPtr);\n"
        "            if ((int)res == 0 && localEnv != null)\n"
        "                env = localEnv;\n"
        "        }\n"
        "    }",
        "JNI.cs Initialize null/AttachCurrentThread guard",
    )
    jni_text = replace_once(
        jni_text,
        "    public static JClass FindClass(string name)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (ClassCache.TryGetValue(name, out JClass? found))\n"
        "            {\n"
        "                return found;\n"
        "            }\n"
        "            else\n"
        "            {\n"
        "                IntPtr nameAnsi = Marshal.StringToHGlobalAnsi(name);\n"
        "                IntPtr res = Env->Functions->FindClass(Env, nameAnsi);\n\n"
        "                Marshal.FreeHGlobal(nameAnsi);\n\n"
        "                using JClass local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "                JClass global = NewGlobalRef<JClass>(local);\n"
        "                ClassCache.Add(name, global);\n"
        "                return global;\n"
        "            }\n"
        "        }\n"
        "    }",
        "    public static JClass FindClass(string name)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            lock (ClassCache)\n"
        "            {\n"
        "                if (ClassCache.TryGetValue(name, out JClass? found) && found != null && found.Valid())\n"
        "                    return NewLocalRef<JClass>(found);\n\n"
        "                IntPtr nameAnsi = Marshal.StringToHGlobalAnsi(name);\n"
        "                IntPtr res = Env->Functions->FindClass(Env, nameAnsi);\n"
        "                Marshal.FreeHGlobal(nameAnsi);\n\n"
        "                if (res == IntPtr.Zero)\n"
        "                    return new JClass { Handle = IntPtr.Zero, ReferenceType = JNI.ReferenceType.Local };\n\n"
        "                using JClass local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "                JClass global = NewGlobalRef<JClass>(local);\n"
        "                ClassCache[name] = global;\n"
        "                return NewLocalRef<JClass>(global);\n"
        "            }\n"
        "        }\n"
        "    }",
        "JNI.cs FindClass thread-safe + return LocalRef",
    )
    write_utf8(jni_path, jni_text)

    # Fix 3: APKAssetManager.cs
    # - Fix DoesAssetExist when path has no '/' (e.g. "lemon_stage.txt", "lemon_patch_date.txt")
    # - Clear pending JNI exception BEFORE returning null in GetAssetStream / GetAssetBytes
    # - Null-check assets array in GetDirectoryContents / DoesAssetExist
    # - Safely obtain Context via UnityPlayer.currentActivity OR ActivityThread.currentApplication()
    #   (critical for Unity 2023/Unity 6 GameActivity apps such as Sonic Dream Team!)
    aam_path = root / "MelonLoader/Utils/APKAssetManager.cs"
    aam_text = aam_path.read_text(encoding="utf-8")
    aam_text = replace_once(
        aam_text,
        "    public static byte[] GetAssetBytes(string path)\n"
        "    {\n"
        "        using JString pathString = JNI.NewString(path);\n"
        "        using JClass assetManagerClass = JNI.GetObjectClass(assetManager);\n"
        "        using JObject asset = JNI.CallObjectMethod<JObject>(assetManager, JNI.GetMethodID(assetManagerClass, \"open\", \"(Ljava/lang/String;)Ljava/io/InputStream;\"), new JValue(pathString));\n"
        "        if (asset == null || !asset.Valid())\n"
        "            return [];",
        "    public static byte[] GetAssetBytes(string path)\n"
        "    {\n"
        "        if (assetManager == null || !assetManager.Valid())\n"
        "            return [];\n"
        "        using JString pathString = JNI.NewString(path);\n"
        "        using JClass assetManagerClass = JNI.GetObjectClass(assetManager);\n"
        "        using JObject asset = JNI.CallObjectMethod<JObject>(assetManager, JNI.GetMethodID(assetManagerClass, \"open\", \"(Ljava/lang/String;)Ljava/io/InputStream;\"), new JValue(pathString));\n"
        "        HandleException();\n"
        "        if (asset == null || !asset.Valid())\n"
        "            return [];",
        "APKAssetManager.cs GetAssetBytes HandleException before return",
    )
    aam_text = replace_once(
        aam_text,
        "    public static Stream? GetAssetStream(string path)\n"
        "    {\n"
        "        using JString pathString = JNI.NewString(path);\n"
        "        using JClass assetManagerClass = JNI.GetObjectClass(assetManager);\n"
        "        JObject asset = JNI.CallObjectMethod<JObject>(assetManager, JNI.GetMethodID(assetManagerClass, \"open\", \"(Ljava/lang/String;)Ljava/io/InputStream;\"), new JValue(pathString));\n"
        "        if (asset == null || !asset.Valid())\n"
        "            return null;\n\n"
        "        HandleException();",
        "    public static Stream? GetAssetStream(string path)\n"
        "    {\n"
        "        if (assetManager == null || !assetManager.Valid())\n"
        "            return null;\n"
        "        using JString pathString = JNI.NewString(path);\n"
        "        using JClass assetManagerClass = JNI.GetObjectClass(assetManager);\n"
        "        JObject asset = JNI.CallObjectMethod<JObject>(assetManager, JNI.GetMethodID(assetManagerClass, \"open\", \"(Ljava/lang/String;)Ljava/io/InputStream;\"), new JValue(pathString));\n"
        "        HandleException();\n"
        "        if (asset == null || !asset.Valid())\n"
        "            return null;",
        "APKAssetManager.cs GetAssetStream HandleException before return",
    )
    aam_text = replace_once(
        aam_text,
        "    public static string[] GetDirectoryContents(string directory)\n"
        "    {\n"
        "        using JString pathString = JNI.NewString(directory);\n"
        "        using JClass assetManagerClass = JNI.GetObjectClass(assetManager);\n"
        "        using JObjectArray<JString> assets = JNI.CallObjectMethod<JObjectArray<JString>>(assetManager, JNI.GetMethodID(assetManagerClass, \"list\", \"(Ljava/lang/String;)[Ljava/lang/String;\"), new JValue(pathString));\n\n"
        "        string[] cleanAssets = [.. assets.Select(a => a.GetString())];\n"
        "        HandleException();\n\n"
        "        return cleanAssets;\n"
        "    }\n\n"
        "    public static bool DoesAssetExist(string path)\n"
        "    {\n"
        "        // using `list` isn't as fast as just calling open, but this allows the function to not crash on debuggable builds of apps\n"
        "        string containingDir = path[..path.LastIndexOf('/')];\n"
        "        using JString pathString = JNI.NewString(containingDir);\n"
        "        using JClass assetManagerClass = JNI.GetObjectClass(assetManager);\n"
        "        using JObjectArray<JString> assets = JNI.CallObjectMethod<JObjectArray<JString>>(assetManager, JNI.GetMethodID(assetManagerClass, \"list\", \"(Ljava/lang/String;)[Ljava/lang/String;\"), new JValue(pathString));\n\n"
        "        bool exists = assets.Any(js =>\n"
        "        {\n"
        "            string asset = JNI.GetJStringString(js);\n"
        "            return path.EndsWith(asset);\n"
        "        });\n\n"
        "        HandleException();\n\n"
        "        return exists;\n"
        "    }",
        "    public static string[] GetDirectoryContents(string directory)\n"
        "    {\n"
        "        if (assetManager == null || !assetManager.Valid())\n"
        "            return [];\n"
        "        using JString pathString = JNI.NewString(directory);\n"
        "        using JClass assetManagerClass = JNI.GetObjectClass(assetManager);\n"
        "        using JObjectArray<JString> assets = JNI.CallObjectMethod<JObjectArray<JString>>(assetManager, JNI.GetMethodID(assetManagerClass, \"list\", \"(Ljava/lang/String;)[Ljava/lang/String;\"), new JValue(pathString));\n"
        "        HandleException();\n"
        "        if (assets == null || !assets.Valid())\n"
        "            return [];\n\n"
        "        return [.. assets.Select(a => a.GetString())];\n"
        "    }\n\n"
        "    public static bool DoesAssetExist(string path)\n"
        "    {\n"
        "        if (assetManager == null || !assetManager.Valid())\n"
        "            return false;\n"
        "        int slash = path.LastIndexOf('/');\n"
        "        string containingDir = slash >= 0 ? path[..slash] : \"\";\n"
        "        string fileName = slash >= 0 ? path[(slash + 1)..] : path;\n"
        "        using JString pathString = JNI.NewString(containingDir);\n"
        "        using JClass assetManagerClass = JNI.GetObjectClass(assetManager);\n"
        "        using JObjectArray<JString> assets = JNI.CallObjectMethod<JObjectArray<JString>>(assetManager, JNI.GetMethodID(assetManagerClass, \"list\", \"(Ljava/lang/String;)[Ljava/lang/String;\"), new JValue(pathString));\n"
        "        HandleException();\n"
        "        if (assets == null || !assets.Valid())\n"
        "            return false;\n\n"
        "        return assets.Any(js => string.Equals(JNI.GetJStringString(js), fileName, StringComparison.Ordinal));\n"
        "    }",
        "APKAssetManager.cs GetDirectoryContents + DoesAssetExist safe root/null handling",
    )
    aam_text = replace_once(
        aam_text,
        "    private static void GetAndroidAssetManager()\n"
        "    {\n"
        "        if (assetManager?.Valid() ?? false)\n"
        "            return;\n\n"
        "        using JClass unityClass = JNI.FindClass(\"com/unity3d/player/UnityPlayer\");\n"
        "        JFieldID activityFieldId = JNI.GetStaticFieldID(unityClass, \"currentActivity\", \"Landroid/app/Activity;\");\n"
        "        using JObject currentActivityObj = JNI.GetStaticObjectField<JObject>(unityClass, activityFieldId);\n"
        "        using JClass activityClass = JNI.GetObjectClass(currentActivityObj);\n"
        "        JObject assetManagerObj = JNI.CallObjectMethod<JObject>(currentActivityObj, JNI.GetMethodID(activityClass, \"getAssets\", \"()Landroid/content/res/AssetManager;\"));\n\n"
        "        HandleException();\n\n"
        "        assetManager = assetManagerObj;\n"
        "    }",
        "    private static JObject? TryGetAndroidContext()\n"
        "    {\n"
        "        using JClass unityClass = JNI.FindClass(\"com/unity3d/player/UnityPlayer\");\n"
        "        HandleException();\n"
        "        if (unityClass.Valid())\n"
        "        {\n"
        "            JFieldID activityFieldId = JNI.GetStaticFieldID(unityClass, \"currentActivity\", \"Landroid/app/Activity;\");\n"
        "            HandleException();\n"
        "            if (activityFieldId.Handle != IntPtr.Zero)\n"
        "            {\n"
        "                JObject act = JNI.GetStaticObjectField<JObject>(unityClass, activityFieldId);\n"
        "                HandleException();\n"
        "                if (act != null && act.Valid())\n"
        "                    return act;\n"
        "                act?.Dispose();\n"
        "            }\n"
        "        }\n\n"
        "        using JClass activityThread = JNI.FindClass(\"android/app/ActivityThread\");\n"
        "        HandleException();\n"
        "        if (activityThread.Valid())\n"
        "        {\n"
        "            JMethodID curAppId = JNI.GetStaticMethodID(activityThread, \"currentApplication\", \"()Landroid/app/Application;\");\n"
        "            HandleException();\n"
        "            if (curAppId.Handle != IntPtr.Zero)\n"
        "            {\n"
        "                JObject app = JNI.CallStaticObjectMethod<JObject>(activityThread, curAppId);\n"
        "                HandleException();\n"
        "                if (app != null && app.Valid())\n"
        "                    return app;\n"
        "                app?.Dispose();\n"
        "            }\n"
        "        }\n\n"
        "        using JClass appGlobals = JNI.FindClass(\"android/app/AppGlobals\");\n"
        "        HandleException();\n"
        "        if (appGlobals.Valid())\n"
        "        {\n"
        "            JMethodID initAppId = JNI.GetStaticMethodID(appGlobals, \"getInitialApplication\", \"()Landroid/app/Application;\");\n"
        "            HandleException();\n"
        "            if (initAppId.Handle != IntPtr.Zero)\n"
        "            {\n"
        "                JObject app = JNI.CallStaticObjectMethod<JObject>(appGlobals, initAppId);\n"
        "                HandleException();\n"
        "                if (app != null && app.Valid())\n"
        "                    return app;\n"
        "                app?.Dispose();\n"
        "            }\n"
        "        }\n\n"
        "        return null;\n"
        "    }\n\n"
        "    private static void GetAndroidAssetManager()\n"
        "    {\n"
        "        if (assetManager?.Valid() ?? false)\n"
        "            return;\n\n"
        "        using JObject? contextObj = TryGetAndroidContext();\n"
        "        if (contextObj == null || !contextObj.Valid())\n"
        "            return;\n\n"
        "        using JClass contextClass = JNI.GetObjectClass(contextObj);\n"
        "        HandleException();\n"
        "        JMethodID getAssetsId = JNI.GetMethodID(contextClass, \"getAssets\", \"()Landroid/content/res/AssetManager;\");\n"
        "        HandleException();\n"
        "        if (getAssetsId.Handle == IntPtr.Zero)\n"
        "            return;\n\n"
        "        using JObject assetManagerObj = JNI.CallObjectMethod<JObject>(contextObj, getAssetsId);\n"
        "        HandleException();\n"
        "        if (assetManagerObj != null && assetManagerObj.Valid())\n"
        "            assetManager = JNI.NewGlobalRef<JObject>(assetManagerObj);\n"
        "    }",
        "APKAssetManager.cs TryGetAndroidContext fallback",
    )
    write_utf8(aam_path, aam_text)
    print("[patched] MelonLoader/JNI/JObject.cs, JNI.cs, and APKAssetManager.cs")


def write_early_crash_c_helper(root: Path) -> None:
    c_path = root / "MelonLoader.Bootstrap/Deps/linux-bionic-arm64/early_crash_signal.c"
    c_code = r"""#include <android/log.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <ucontext.h>
#include <unistd.h>

static char g_internal_log_path[512] = "/data/local/tmp/lemon_early_crash.log";
static char g_last_checkpoint[256] = "none";
static int g_handlers_installed = 0;

static struct sigaction g_old_sigsegv;
static struct sigaction g_old_sigabrt;
static struct sigaction g_old_sigbus;
static struct sigaction g_old_sigill;

static const char *sig_name(int sig) {
    switch (sig) {
        case SIGSEGV: return "SIGSEGV";
        case SIGABRT: return "SIGABRT";
        case SIGBUS:  return "SIGBUS";
        case SIGILL:  return "SIGILL";
        default:      return "SIGNAL";
    }
}

static void append_file_line(const char *path, const char *line) {
    if (!path || !path[0] || !line) return;
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0664);
    if (fd < 0) return;
    size_t len = strlen(line);
    if (len > 0) {
        (void)write(fd, line, len);
    }
    (void)write(fd, "\n", 1);
    (void)fsync(fd);
    close(fd);
}

static void crash_signal_handler(int sig, siginfo_t *info, void *uctx) {
    uint64_t pc = 0, lr = 0, sp = 0;
#if defined(__aarch64__)
    if (uctx) {
        ucontext_t *uc = (ucontext_t *)uctx;
        pc = (uint64_t)uc->uc_mcontext.pc;
        lr = (uint64_t)uc->uc_mcontext.regs[30];
        sp = (uint64_t)uc->uc_mcontext.sp;
    }
#endif
    uintptr_t fault = info ? (uintptr_t)info->si_addr : 0;
    int code = info ? info->si_code : 0;

    Dl_info dl_pc;
    memset(&dl_pc, 0, sizeof(dl_pc));
    const char *mod_pc = "<unknown>";
    uintptr_t off_pc = 0;
    if (pc && dladdr((void *)(uintptr_t)pc, &dl_pc) && dl_pc.dli_fname) {
        mod_pc = dl_pc.dli_fname;
        off_pc = (uintptr_t)pc - (uintptr_t)dl_pc.dli_fbase;
    }

    Dl_info dl_lr;
    memset(&dl_lr, 0, sizeof(dl_lr));
    const char *mod_lr = "<unknown>";
    uintptr_t off_lr = 0;
    if (lr && dladdr((void *)(uintptr_t)lr, &dl_lr) && dl_lr.dli_fname) {
        mod_lr = dl_lr.dli_fname;
        off_lr = (uintptr_t)lr - (uintptr_t)dl_lr.dli_fbase;
    }

    char buf[1280];
    snprintf(
        buf,
        sizeof(buf),
        "[MelonLoader073][FATAL_SIGNAL] %s (%d) code=%d fault=0x%lx pc=0x%llx (%s+0x%lx) lr=0x%llx (%s+0x%lx) sp=0x%llx last=[%s]",
        sig_name(sig),
        sig,
        code,
        (unsigned long)fault,
        (unsigned long long)pc,
        mod_pc,
        (unsigned long)off_pc,
        (unsigned long long)lr,
        mod_lr,
        (unsigned long)off_lr,
        (unsigned long long)sp,
        g_last_checkpoint
    );

    __android_log_write(ANDROID_LOG_FATAL, "LemonEarly", buf);
    __android_log_write(ANDROID_LOG_FATAL, "MelonLoader", buf);
    append_file_line(g_internal_log_path, buf);

    struct sigaction *old_act = NULL;
    if (sig == SIGSEGV) old_act = &g_old_sigsegv;
    else if (sig == SIGABRT) old_act = &g_old_sigabrt;
    else if (sig == SIGBUS) old_act = &g_old_sigbus;
    else if (sig == SIGILL) old_act = &g_old_sigill;

    if (old_act) {
        if ((old_act->sa_flags & SA_SIGINFO) && old_act->sa_sigaction) {
            old_act->sa_sigaction(sig, info, uctx);
            return;
        }
        if (old_act->sa_handler != SIG_DFL && old_act->sa_handler != SIG_IGN && old_act->sa_handler != NULL) {
            old_act->sa_handler(sig);
            return;
        }
    }

    signal(sig, SIG_DFL);
    raise(sig);
}

void lemon_install_signal_handlers(const char *internal_log_path) {
    if (internal_log_path && internal_log_path[0]) {
        snprintf(g_internal_log_path, sizeof(g_internal_log_path), "%s", internal_log_path);
    }
    if (g_handlers_installed) return;
    g_handlers_installed = 1;

    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_SIGINFO;
    sa.sa_sigaction = crash_signal_handler;

    sigaction(SIGSEGV, &sa, &g_old_sigsegv);
    sigaction(SIGABRT, &sa, &g_old_sigabrt);
    sigaction(SIGBUS, &sa, &g_old_sigbus);
    sigaction(SIGILL, &sa, &g_old_sigill);
}

void lemon_early_log_write(const char *internal_path, const char *external_path, const char *line, int prio) {
    if (!line) return;
    snprintf(g_last_checkpoint, sizeof(g_last_checkpoint), "%s", line);
    __android_log_write(prio, "LemonEarly", line);
    __android_log_write(prio, "MelonLoader", line);
    if (internal_path && internal_path[0]) {
        append_file_line(internal_path, line);
    }
    if (external_path && external_path[0]) {
        append_file_line(external_path, line);
    }
}
"""
    write_utf8(c_path, c_code)
    print(f"[wrote] {c_path}")


def patch_directory_build_props(root: Path) -> None:
    path = root / "Directory.Build.props"
    props = """<Project>
    <PropertyGroup>
        <Version>0.7.3</Version>
        <AndroidBuild Condition="'$(RuntimeIdentifier.StartsWith(`linux-bionic`))' == 'true' Or '$(AndroidBuild)' == 'true'">true</AndroidBuild>
        <AppHostVersion>10.0.8</AppHostVersion>
        <AppHostVersion Condition="'$(AndroidBuild)' == 'true'">9.0.0</AppHostVersion>

        <BootstrapTarget>net9</BootstrapTarget>

        <DotNetTarget>net6</DotNetTarget>
        <DotNetTarget Condition="'$(AndroidBuild)' == 'true'">net8.0</DotNetTarget>

        <MonoTarget>net35</MonoTarget>
        <MonoBleedingEdgeTarget>net472</MonoBleedingEdgeTarget>

        <PortablePdbToMdbTarget>netstandard2.0</PortablePdbToMdbTarget>

        <Authors>Lava Gang</Authors>
        <Company>discord.gg/2Wn3N2P</Company>
        <Copyright>Copyright (c) 2020 - 2025 Lava Gang</Copyright>
        <Description>The World's First Universal Mod Loader for Unity Games compatible with both Il2Cpp and Mono.</Description>

        <RepositoryUrl>https://github.com/LavaGang/MelonLoader</RepositoryUrl>
        <RepositoryType>git</RepositoryType>

        <LangVersion>Latest</LangVersion>
        <IsPackable>false</IsPackable>
        <Configurations>Debug;Release</Configurations>

        <Platforms>x86;x64;arm64</Platforms>
        <RuntimeIdentifiers>linux-bionic-arm64;win-x86;win-x64;linux-x64;osx-x64</RuntimeIdentifiers>

        <AppendRuntimeIdentifierToOutputPath>false</AppendRuntimeIdentifierToOutputPath>
        <AppendTargetFrameworkToOutputPath>false</AppendTargetFrameworkToOutputPath>
        <SuppressTfmSupportBuildWarnings>true</SuppressTfmSupportBuildWarnings>
        <CETCompat>false</CETCompat>
        <AllowUnsafeBlocks>true</AllowUnsafeBlocks>
        <NoWarn>LNK4099;CS8632;SYSLIB0003;SYSLIB0014</NoWarn>
        <InvariantGlobalization>true</InvariantGlobalization>

        <RestoreAdditionalProjectSources>https://nuget.bepinex.dev/v3/index.json</RestoreAdditionalProjectSources>
    </PropertyGroup>

    <PropertyGroup>
        <IcedVersion>1.21.0</IcedVersion>
        <TomletVersion>6.2.0</TomletVersion>
        <PastelVersion>7.0.1</PastelVersion>
        <MonoModVersion>22.7.31.1</MonoModVersion>
        <MonoCecilVersion>0.11.6</MonoCecilVersion>
        <HarmonyXVersion>2.10.2</HarmonyXVersion>
        <AssetToolsVersion>3.0.4</AssetToolsVersion>
        <AssetRipperVersion>3.2.0</AssetRipperVersion>
        <AsmResolverVersion>6.0.0-beta.5</AsmResolverVersion>
        <Il2CppInteropVersion>1.5.1-ci.845</Il2CppInteropVersion>
    </PropertyGroup>

    <PropertyGroup Condition="'$(TargetFramework)' == '$(MonoTarget)'">
        <MonoCecilVersion>0.10.4</MonoCecilVersion>
    </PropertyGroup>

    <PropertyGroup Condition="'$(ForceRID)' != ''">
        <RuntimeIdentifier>$(ForceRID)</RuntimeIdentifier>
    </PropertyGroup>

    <PropertyGroup Condition="'$(RuntimeIdentifier)' == '' And '$(AndroidBuild)' == 'true'">
        <RuntimeIdentifier>linux-bionic-arm64</RuntimeIdentifier>
    </PropertyGroup>

    <PropertyGroup Condition="'$(RuntimeIdentifier)' == '' And '$([System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform($([System.Runtime.InteropServices.OSPlatform]::Windows)))' == 'true'">
        <RuntimeIdentifier>win-$(Platform)</RuntimeIdentifier>
    </PropertyGroup>

    <PropertyGroup Condition="'$(RuntimeIdentifier)' == '' And '$([System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform($([System.Runtime.InteropServices.OSPlatform]::Linux)))' == 'true'">
        <RuntimeIdentifier>linux-x64</RuntimeIdentifier>
    </PropertyGroup>

    <PropertyGroup Condition="'$(RuntimeIdentifier)' == '' And '$([System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform($([System.Runtime.InteropServices.OSPlatform]::OSX)))' == 'true'">
        <RuntimeIdentifier>osx-x64</RuntimeIdentifier>
    </PropertyGroup>

    <PropertyGroup Condition="'$(RuntimeIdentifier.StartsWith(`linux-bionic`))' == 'true' Or '$(AndroidBuild)' == 'true'">
        <PlatformTarget>arm64</PlatformTarget>
        <DefineConstants>$(DefineConstants);ANDROID;NET6_0;NET6_0_OR_GREATER</DefineConstants>
        <LibPrefix>lib</LibPrefix>
        <LibExt>.a</LibExt>
    </PropertyGroup>

    <PropertyGroup Condition="'$(RuntimeIdentifier.StartsWith(`linux-`))' == 'true' And '$(RuntimeIdentifier.StartsWith(`linux-bionic`))' == 'false'">
        <DefineConstants>$(DefineConstants);LINUX</DefineConstants>
        <LibPrefix>lib</LibPrefix>
        <LibExt>.a</LibExt>
    </PropertyGroup>

    <PropertyGroup Condition="'$(RuntimeIdentifier.StartsWith(`osx-`))' == 'true'">
        <DefineConstants>$(DefineConstants);OSX</DefineConstants>
        <LibPrefix>lib</LibPrefix>
        <LibExt>.a</LibExt>
    </PropertyGroup>

    <PropertyGroup Condition="'$(RuntimeIdentifier.StartsWith(`win-`))' == 'true'">
        <DefineConstants>$(DefineConstants);WINDOWS</DefineConstants>
        <LibExt>.lib</LibExt>
    </PropertyGroup>

    <PropertyGroup Condition="'$(RuntimeIdentifier.EndsWith(`-x64`))' == 'true'">
        <DefineConstants>$(DefineConstants);X64</DefineConstants>
    </PropertyGroup>

    <PropertyGroup>
        <MLOutDir>$(SolutionDir)Output/$(Configuration)/$(RuntimeIdentifier)</MLOutDir>
    </PropertyGroup>

    <ItemGroup Condition="'$(TargetFramework)' == '$(MonoTarget)' Or '$(TargetFramework)' == '$(MonoBleedingEdgeTarget)'">
        <ProjectReference Include="$(SolutionDir)/PortablePdbToMdb/PortablePdbToMdb.csproj"
                          ReferenceOutputAssembly="false"
                          SkipGetTargetFrameworkProperties="true"
                          GlobalPropertiesToRemove="TargetFramework" />
    </ItemGroup>
    <UsingTask TaskName="PortablePdbToMdb"
               Condition="'$(TargetFramework)' == '$(MonoTarget)' Or '$(TargetFramework)' == '$(MonoBleedingEdgeTarget)'"
               AssemblyFile="$(SolutionDir)/PortablePdbToMdb/bin/PortablePdbToMdb.dll"
               TaskFactory="TaskHostFactory" />
    <Target Name="SymbolsConvert" Condition="'$(TargetFramework)' == 'net35'" AfterTargets="AfterBuild">
        <PortablePdbToMdb AssemblyPath="$(TargetPath)" />
    </Target>
</Project>
"""
    write_utf8(path, props)
    print(f"[patched] {path} (v0.7.3 + linux-bionic-arm64 Android support)")


def patch_bootstrap_targets_and_csproj(root: Path) -> None:
    bionic_targets = """<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">

    <PropertyGroup>
        <IsAndroid>$(RuntimeIdentifier.ToLower().StartsWith('android'))</IsAndroid>
        <IsAndroid Condition="'$(IsAndroid)'=='false'">$(RuntimeIdentifier.ToLower().StartsWith('linux-bionic'))</IsAndroid>
        <IsWindowsHost>$([MSBuild]::IsOSPlatform('Windows'))</IsWindowsHost>
        <IsLinuxHost>$([MSBuild]::IsOSPlatform('Linux'))</IsLinuxHost>
        <IsOSXHost>$([MSBuild]::IsOSPlatform('OSX'))</IsOSXHost>
        <RemoveSections Condition="'$(RemoveSections)' != '' And $([System.String]::ToLower('$(RemoveSections)')) != 'false'">true</RemoveSections>
    </PropertyGroup>

    <PropertyGroup Condition="$(IsAndroid) == 'true'">
        <DisableUnsupportedError>true</DisableUnsupportedError>
        <PublishAotUsingRuntimePack>true</PublishAotUsingRuntimePack>
        <NdkPropertiesFile>$(ANDROID_NDK_ROOT)/source.properties</NdkPropertiesFile>
        <NdkHost Condition="$(IsWindowsHost) == 'true'">windows-x86_64</NdkHost>
        <NdkHost Condition="$(IsLinuxHost) == 'true'">linux-x86_64</NdkHost>
        <NdkHost Condition="$(IsOSXHost) == 'true'">darwin-x86_64</NdkHost>
        <SysRoot Condition="'$(SysRoot)' == ''">$(ANDROID_NDK_ROOT)/toolchains/llvm/prebuilt/$(NdkHost)/sysroot</SysRoot>
        <BaseRevision Condition="Exists('$(NdkPropertiesFile)')">$([System.IO.File]::ReadAllText('$(NdkPropertiesFile)'))</BaseRevision>
        <NdkVersion>$([System.Text.RegularExpressions.Regex]::Match('$(BaseRevision)', 'Pkg.BaseRevision\\s*=\\s*(\\S+)').Groups[1].Value.Split('.')[0])</NdkVersion>
        <NdkVersion Condition="'$(NdkVersion)' == ''">26</NdkVersion>
    </PropertyGroup>

    <PropertyGroup Condition="$(IsAndroid) == 'true'">
        <CppCompilerAndLinker Condition="$(IsWindowsHost) != 'true'">$(ANDROID_NDK_ROOT)/toolchains/llvm/prebuilt/$(NdkHost)/bin/clang++</CppCompilerAndLinker>
        <ObjCopyName Condition="$(IsWindowsHost) != 'true'">$(ANDROID_NDK_ROOT)/toolchains/llvm/prebuilt/$(NdkHost)/bin/llvm-objcopy</ObjCopyName>
        <CppCompilerAndLinker Condition="$(IsWindowsHost) == 'true'">android_clang.cmd</CppCompilerAndLinker>
        <ObjCopyName Condition="$(IsWindowsHost) == 'true'">android_llvm-objcopy.cmd</ObjCopyName>
    </PropertyGroup>

    <ItemGroup Condition="$(IsAndroid) == 'true'">
        <LinkerArg Include="-z noexecstack" />
        <LinkerArg Include="-Wl,-z,max-page-size=16384" />
        <LinkerArg Include="-Wl,--allow-multiple-definition" />
        <LinkerArg Include="-static-libstdc++" />
        <LinkerArg Include="-llog" />
        <LinkerArg Include="-ldl" />
        <LinkerArg Condition="$(RuntimeIdentifier.ToLower().EndsWith('-x64'))" Include="--target=x86_64-linux-android21" />
        <LinkerArg Condition="$(RuntimeIdentifier.ToLower().EndsWith('-arm64'))" Include="--target=aarch64-linux-android21" />
    </ItemGroup>

    <Target Condition="$(IsAndroid) == 'true'" Name="BuildEarlyCrashSignalObj" BeforeTargets="IlcCompile;LinkNative">
        <PropertyGroup>
            <_NdkClang Condition="$(IsWindowsHost) != 'true'">$(ANDROID_NDK_ROOT)/toolchains/llvm/prebuilt/$(NdkHost)/bin/clang</_NdkClang>
            <_NdkClang Condition="$(IsWindowsHost) == 'true'">$(ANDROID_NDK_ROOT)/toolchains/llvm/prebuilt/$(NdkHost)/bin/clang.exe</_NdkClang>
        </PropertyGroup>
        <Exec Command="&quot;$(_NdkClang)&quot; --target=aarch64-linux-android21 -O2 -fPIC -c Deps/linux-bionic-arm64/early_crash_signal.c -o Deps/linux-bionic-arm64/early_crash_signal.o" />
    </Target>

    <Target Condition="$(IsAndroid) == 'true'" Name="PrepareNdkBatches" AfterTargets="Build" BeforeTargets="IlcCompile">
        <PropertyGroup>
            <NdkHostBin Condition="$(IsWindowsHost) == 'true'">$(ANDROID_NDK_ROOT)\\toolchains\\llvm\\prebuilt\\$(NdkHost)\\bin\\</NdkHostBin>
            <NdkHostBin Condition="$(IsWindowsHost) != 'true'">$(ANDROID_NDK_ROOT)/toolchains/llvm/prebuilt/$(NdkHost)/bin/</NdkHostBin>
        </PropertyGroup>
        <WriteLinesToFile Condition="$(IsWindowsHost) == 'true'" File="android_clang.cmd" Lines="&quot;$(NdkHostBin)clang++.exe&quot; %*" Overwrite="true" />
        <WriteLinesToFile Condition="$(IsWindowsHost) == 'true'" File="android_llvm-objcopy.cmd" Lines="&quot;$(NdkHostBin)llvm-objcopy.exe&quot; %*" Overwrite="true" />
    </Target>

    <Target Condition="$(IsAndroid) == 'true'" Name="RemoveBatchFiles" AfterTargets="LinkNative">
        <ItemGroup>
            <BatchFiles Condition="$(IsWindowsHost) == 'true'" Include="android_clang.cmd" />
            <BatchFiles Condition="$(IsWindowsHost) == 'true'" Include="android_llvm-objcopy.cmd" />
        </ItemGroup>
        <Delete Files="@(BatchFiles)" />
    </Target>

    <Target Name="RemoveSections" Condition="$(IsAndroid) == 'true'" AfterTargets="IlcCompile" BeforeTargets="LinkNative">
        <PropertyGroup Condition="$(RemoveSections) != 'true'">
            <RemoveSections Condition="$([System.Int32]::Parse($(NdkVersion.Split('.')[0]))) >= 26">true</RemoveSections>
        </PropertyGroup>
        <WriteLinesToFile Condition="$(RemoveSections) == 'true'" File="$(ExportsFile)" Lines="$([System.IO.File]::ReadAllText($(ExportsFile)).Replace('global: _init; _fini;','global: '))" Overwrite="true" />
    </Target>
</Project>
"""
    write_utf8(root / "MelonLoader.Bootstrap/BionicNativeAot.targets", bionic_targets)

    android_exports = """{
    global:
        DotNetRuntimeDebugHeader;
        NativeHookAttach;
        NativeHookDetach;
        LogMsg;
        LogError;
        LogMelonInfo;
        MonoInstallHooks;
        MonoGetDomainPtr;
        MonoGetRuntimeHandle;
        IsConsoleOpen;
        GetLoaderConfig;
        GetJavaVM;
        JNI_OnLoad;
    local: *;
};
"""
    write_utf8(root / "MelonLoader.Bootstrap/androidexports.def", android_exports)

    csproj_path = root / "MelonLoader.Bootstrap/MelonLoader.Bootstrap.csproj"
    csproj_text = csproj_path.read_text(encoding="utf-8")

    csproj_text = replace_once(
        csproj_text,
        "        <XwinArches Condition=\"'$(XWinArches)' == ''\">x86,x86_64</XwinArches>\n    </PropertyGroup>",
        "        <XwinArches Condition=\"'$(XWinArches)' == ''\">x86,x86_64</XwinArches>\n"
        "        <AssemblyName Condition=\"'$(RuntimeIdentifier.StartsWith(`linux-bionic-`))' == 'true'\">libmain</AssemblyName>\n"
        "    </PropertyGroup>\n\n"
        "    <Import Condition=\"'$(RuntimeIdentifier.StartsWith(`linux-bionic-`))' == 'true'\" Project=\"BionicNativeAot.targets\" />",
        "Bootstrap csproj AssemblyName + BionicNativeAot import",
    )

    csproj_text = replace_once(
        csproj_text,
        "    <PropertyGroup Condition=\"'$(RuntimeIdentifier.StartsWith(`linux-`))' == 'true'\">\n"
        "        <ExportsFile>linuxexports.def</ExportsFile>\n"
        "        <LinkStandardCPlusPlusLibrary>true</LinkStandardCPlusPlusLibrary>\n"
        "    </PropertyGroup>",
        "    <PropertyGroup Condition=\"'$(RuntimeIdentifier.StartsWith(`linux-`))' == 'true' And '$(RuntimeIdentifier.StartsWith(`linux-bionic-`))' == 'false'\">\n"
        "        <ExportsFile>linuxexports.def</ExportsFile>\n"
        "        <LinkStandardCPlusPlusLibrary>true</LinkStandardCPlusPlusLibrary>\n"
        "    </PropertyGroup>\n\n"
        "    <PropertyGroup Condition=\"'$(RuntimeIdentifier.StartsWith(`linux-bionic-`))' == 'true'\">\n"
        "        <ExportsFile>androidexports.def</ExportsFile>\n"
        "        <LinkStandardCPlusPlusLibrary>false</LinkStandardCPlusPlusLibrary>\n"
        "    </PropertyGroup>",
        "Bootstrap csproj ExportsFile",
    )

    csproj_text = replace_once(
        csproj_text,
        "        <PackageReference Include=\"PublishAotCrossXWin\" Version=\"1.2.0\" PrivateAssets=\"all\" />",
        "        <PackageReference Include=\"PublishAotCrossXWin\" Version=\"1.2.0\" PrivateAssets=\"all\" Condition=\"'$(RuntimeIdentifier.StartsWith(`linux-bionic-`))' == 'false'\" />",
        "Bootstrap csproj PublishAotCrossXWin condition",
    )

    csproj_text = replace_once(
        csproj_text,
        "    <ItemGroup>\n        <Compile Include=\"$(SolutionDir)/MelonLoader/LoaderConfig.cs\" />\n    </ItemGroup>",
        "    <ItemGroup>\n"
        "        <Compile Include=\"$(SolutionDir)/MelonLoader/LoaderConfig.cs\" />\n"
        "    </ItemGroup>\n\n"
        "    <ItemGroup Condition=\"'$(RuntimeIdentifier.StartsWith(`linux-bionic-`))' == 'true'\">\n"
        "        <Compile Include=\"$(SolutionDir)/MelonLoader/JNI/*.cs\" Link=\"JNI/%(Filename)%(Extension)\" />\n"
        "        <Compile Include=\"$(SolutionDir)/MelonLoader/Utils/APKAssetManager.cs\" Link=\"Proxy/Android/%(Filename)%(Extension)\" />\n"
        "        <NativeLibrary Include=\"Deps/linux-bionic-arm64/early_crash_signal.o\" />\n"
        "    </ItemGroup>",
        "Bootstrap csproj Android JNI + early_crash_signal.o",
    )

    csproj_text = replace_once(
        csproj_text,
        "    <Target Name=\"PublishAfterBuild\" AfterTargets=\"Build\" Condition=\"'$(SkipPublishAfterBuild)' != 'true'\">",
        "    <Target Name=\"PublishAfterBuild\" AfterTargets=\"Build\" Condition=\"'$(SkipPublishAfterBuild)' != 'true' And '$(RuntimeIdentifier.StartsWith(`linux-bionic-`))' == 'false'\">",
        "Bootstrap csproj PublishAfterBuild",
    )

    write_utf8(csproj_path, csproj_text)
    print(f"[patched] {csproj_path}")


def write_android_proxy_and_early_log(root: Path) -> None:
    proxy_dir = root / "MelonLoader.Bootstrap/Proxy/Android"

    early_log_cs = """#if ANDROID
using System.Runtime.InteropServices;

namespace MelonLoader.Bootstrap.Proxy.Android;

internal static partial class EarlyLog
{
    private static string? _internalPath;
    private static string? _externalPath;
    private static bool _initialized;

    public static string Stage { get; private set; } = "full";

    [LibraryImport("*", EntryPoint = "lemon_install_signal_handlers", StringMarshalling = StringMarshalling.Utf8)]
    private static partial void InstallSignalHandlersNative(string internalLogPath);

    [LibraryImport("*", EntryPoint = "lemon_early_log_write", StringMarshalling = StringMarshalling.Utf8)]
    private static partial void WriteEarlyLogNative(string? internalPath, string? externalPath, string line, int prio);

    public static string? ReadPackageNameFromCmdline()
    {
        try
        {
            byte[] bytes = File.ReadAllBytes("/proc/self/cmdline");
            int end = Array.IndexOf(bytes, (byte)0);
            if (end < 0) end = bytes.Length;
            string s = System.Text.Encoding.UTF8.GetString(bytes, 0, end).Trim();
            return string.IsNullOrEmpty(s) ? null : s;
        }
        catch
        {
            return null;
        }
    }

    public static void Init(string? pkgOverride = null)
    {
        if (_initialized && string.IsNullOrEmpty(pkgOverride))
            return;

        string? pkg = !string.IsNullOrWhiteSpace(pkgOverride) ? pkgOverride : ReadPackageNameFromCmdline();
        if (!string.IsNullOrWhiteSpace(pkg))
        {
            try
            {
                string intDir = $"/data/data/{pkg}/files";
                Directory.CreateDirectory(intDir);
                _internalPath = Path.Combine(intDir, "lemon_early_crash.log");
            }
            catch { }

            try
            {
                string extDir = $"/sdcard/Android/data/{pkg}/files";
                Directory.CreateDirectory(extDir);
                _externalPath = Path.Combine(extDir, "lemon_early_crash.log");
            }
            catch { }
        }

        try
        {
            InstallSignalHandlersNative(_internalPath ?? "");
        }
        catch { }

        _initialized = true;
    }

    public static void SetStage(string? stageRaw)
    {
        string normalized = (stageRaw ?? "full").Trim().ToLowerInvariant();
        if (normalized is "full" or "no_hooks" or "minimal_bootstrap" or "il2cpp_init_only")
            Stage = normalized;
        else
            Stage = "full";
        Checkpoint("stage", $"Configured stage={Stage}");
    }

    public static void Checkpoint(string stage, string msg)
    {
        string line = $"[MelonLoader073][{stage}] {msg}";
        try
        {
            WriteEarlyLogNative(_internalPath, _externalPath, line, 4);
        }
        catch
        {
            try
            {
                if (!string.IsNullOrEmpty(_internalPath))
                    File.AppendAllText(_internalPath, line + "\\n");
            }
            catch { }
        }
    }
}
#endif
"""
    write_utf8(proxy_dir / "EarlyLog.cs", early_log_cs)

    std_redirect_cs = """#if ANDROID
using System.Runtime.InteropServices;
using System.Text;

namespace MelonLoader.Bootstrap.Proxy.Android;

internal static class StdRedirect
{
    private const int STDERR_FILENO = 2;
    private const int STDOUT_FILENO = 1;

    [DllImport("libc.so", SetLastError = true)]
    private static extern int pipe(int[] pipefd);

    [DllImport("libc.so", SetLastError = true)]
    private static extern int dup2(int oldfd, int newfd);

    [DllImport("libc.so", SetLastError = true)]
    private static extern IntPtr fdopen(int fd, string mode);

    [DllImport("libc.so", SetLastError = true)]
    private static extern IntPtr fgets(byte[] buffer, int size, IntPtr stream);

    [DllImport("liblog.so", SetLastError = true)]
    private static extern int __android_log_write(int prio, string tag, string text);

    public static void RedirectStdErr()
    {
        Environment.SetEnvironmentVariable("COREHOST_TRACE", "1");
        Environment.SetEnvironmentVariable("COREHOST_TRACE_VERBOSITY", "3");
        RedirectStream(STDERR_FILENO, "MelonLoader");
    }

    public static void RedirectStdOut()
    {
        Environment.SetEnvironmentVariable("COREHOST_TRACE", "1");
        Environment.SetEnvironmentVariable("COREHOST_TRACE_VERBOSITY", "3");
        RedirectStream(STDOUT_FILENO, "MelonLoader");
    }

    private static void RedirectStream(int fileno, string tag)
    {
        int[] pipes = new int[2];
        if (pipe(pipes) != 0)
            return;

        dup2(pipes[1], fileno);
        IntPtr inputFile = fdopen(pipes[0], "r");
        if (inputFile == IntPtr.Zero)
            return;

        Thread logThread = new(() =>
        {
            try
            {
                byte[] buffer = new byte[1024];
                while (true)
                {
                    Array.Clear(buffer, 0, buffer.Length);
                    IntPtr result = fgets(buffer, buffer.Length, inputFile);
                    if (result == IntPtr.Zero)
                        break;

                    int zeroIdx = Array.IndexOf(buffer, (byte)0);
                    int len = zeroIdx >= 0 ? zeroIdx : buffer.Length;
                    string logMsg = Encoding.UTF8.GetString(buffer, 0, len).TrimEnd('\\n', '\\r');
                    if (!string.IsNullOrEmpty(logMsg))
                    {
                        __android_log_write(3, tag, logMsg);
                        if (logMsg.Contains("error", StringComparison.OrdinalIgnoreCase) ||
                            logMsg.Contains("exception", StringComparison.OrdinalIgnoreCase) ||
                            logMsg.Contains("fail", StringComparison.OrdinalIgnoreCase))
                        {
                            EarlyLog.Checkpoint("corehost", logMsg);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                EarlyLog.Checkpoint("stdredirect", $"WARN thread stopped: {ex.Message}");
            }
        });

        logThread.IsBackground = true;
        logThread.Start();
    }
}
#endif
"""
    write_utf8(proxy_dir / "StdRedirect.cs", std_redirect_cs)

    android_proxy_cs = """#if ANDROID
using System.Diagnostics.CodeAnalysis;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.Marshalling;
using MelonLoader.Java;
using MelonLoader.Bootstrap.Utils;

namespace MelonLoader.Bootstrap.Proxy.Android;

public static class AndroidProxy
{
    public static nint UnityHandle { get; private set; }
    public static string? NativeLibraryDir { get; private set; }

    [DllImport("liblog.so", EntryPoint = "__android_log_write", CharSet = CharSet.Ansi)]
    private static extern int LogWriteInternal(int prio, string tag, string text);

    public static void Log(string text)
    {
        try
        {
            LogWriteInternal(4, "MelonLoader", text ?? string.Empty);
        }
        catch { }
    }

    [UnmanagedCallersOnly(CallConvs = [typeof(CallConvCdecl)])]
    [RequiresDynamicCode("Calls MelonLoader.Bootstrap.Proxy.Android.AndroidBootstrap.LoadBootstrap()")]
    private static unsafe byte Load(void* env, void* jobject, void* str)
    {
        EarlyLog.Init();
        EarlyLog.Checkpoint("proxy_load", "NativeLoader.load() entered");

        string? libDir = null;
        if (str != null)
        {
            try
            {
                libDir = new JString { Handle = (nint)str }.GetString();
                NativeLibraryDir = libDir;
                EarlyLog.Checkpoint("proxy_load", $"libDir={libDir}");
            }
            catch (Exception ex)
            {
                EarlyLog.Checkpoint("proxy_load", $"WARN failed reading libDir jstring: {ex.Message}");
            }
        }

        if (!LoadUnity(libDir))
        {
            EarlyLog.Checkpoint("proxy_load", "FATAL failed loading libunity.so");
            return 0;
        }

        try
        {
            AndroidBootstrap.LoadBootstrap();
            return 1;
        }
        catch (Exception ex)
        {
            EarlyLog.Checkpoint("proxy_load", $"FATAL LoadBootstrap exception: {ex}");
            return 0;
        }
    }

    public static unsafe bool LoadUnity(string? libDir)
    {
        nint libUnity = IntPtr.Zero;
        if (!string.IsNullOrWhiteSpace(libDir))
        {
            string candidate = Path.Combine(libDir, "libunity.so");
            EarlyLog.Checkpoint("proxy_load", $"Trying dlopen({candidate})");
            NativeLibrary.TryLoad(candidate, out libUnity);
        }

        if (libUnity == IntPtr.Zero)
        {
            EarlyLog.Checkpoint("proxy_load", "Trying fallback dlopen(libunity.so)");
            NativeLibrary.TryLoad("libunity.so", out libUnity);
        }

        if (libUnity == IntPtr.Zero)
        {
            Log("Failed to load libunity.so");
            return false;
        }

        UnityHandle = libUnity;
        EarlyLog.Checkpoint("proxy_load", $"Loaded libunity.so handle=0x{libUnity:X}");

        if (!NativeFunc.GetExport<JNI_OnLoadFunc>(libUnity, "JNI_OnLoad", out var jniOnLoad))
        {
            EarlyLog.Checkpoint("proxy_load", "WARN libunity.so has no JNI_OnLoad export");
            return true;
        }

        EarlyLog.Checkpoint("proxy_load", "Calling libunity.so::JNI_OnLoad");
        nint res = jniOnLoad((IntPtr)JNI.VM, IntPtr.Zero);
        EarlyLog.Checkpoint("proxy_load", $"libunity.so::JNI_OnLoad returned 0x{res:X}");
        return true;
    }

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    public delegate nint JNI_OnLoadFunc(IntPtr vm, IntPtr reserved);

    [UnmanagedCallersOnly(CallConvs = [typeof(CallConvCdecl)])]
    private static unsafe byte Unload(void* vm, void* reserved)
    {
        EarlyLog.Checkpoint("proxy_unload", "NativeLoader.unload() called");
        Log("Unload called");
        return 1;
    }

    [UnmanagedCallersOnly(EntryPoint = "JNI_OnLoad")]
    public static unsafe JNI.Version JNI_OnLoad(IntPtr vm, void* reserved)
    {
        EarlyLog.Init();
        EarlyLog.Checkpoint("proxy_jni", $"JNI_OnLoad entered vm=0x{vm:X}");

        JNI.Initialize(vm);
        using var nativeLoader = JNI.FindClass("com/unity3d/player/NativeLoader");
        if (JNI.ExceptionCheck())
            JNI.ExceptionClear();
        if (!nativeLoader.Valid())
        {
            EarlyLog.Checkpoint("proxy_jni", "WARN cannot find com/unity3d/player/NativeLoader");
            Log("Cannot find NativeLoader class");
            return JNI.Version.V1_6;
        }

        var methods = (JNINativeMethod*)NativeMemory.Alloc((nuint)(sizeof(JNINativeMethod) * 2));
        methods[0] = new JNINativeMethod
        {
            Name = Utf8StringMarshaller.ConvertToUnmanaged("load"),
            Signature = Utf8StringMarshaller.ConvertToUnmanaged("(Ljava/lang/String;)Z"),
            FnPtr = (delegate* unmanaged[Cdecl]<void*, void*, void*, byte>)&Load
        };
        methods[1] = new JNINativeMethod
        {
            Name = Utf8StringMarshaller.ConvertToUnmanaged("unload"),
            Signature = Utf8StringMarshaller.ConvertToUnmanaged("()Z"),
            FnPtr = (delegate* unmanaged[Cdecl]<void*, void*, byte>)&Unload
        };

        var registerNatives = JNI.Env->Functions->RegisterNatives(JNI.Env, nativeLoader.Handle, (IntPtr)methods, 2);
        if (registerNatives != 0)
        {
            EarlyLog.Checkpoint("proxy_jni", $"ERROR RegisterNatives failed: {registerNatives}");
            Log("Failed to register native methods");
        }
        else
        {
            EarlyLog.Checkpoint("proxy_jni", "Registered NativeLoader.load/unload OK");
        }

        return JNI.Version.V1_6;
    }

    private unsafe struct JNINativeMethod
    {
        public byte* Name;
        public byte* Signature;
        public void* FnPtr;
    }
}
#endif
"""
    write_utf8(proxy_dir / "AndroidProxy.cs", android_proxy_cs)

    android_bootstrap_cs = """#if ANDROID
using System.Diagnostics.CodeAnalysis;
using System.Runtime.InteropServices;
using System.Text;
using MelonLoader.Java;
using MelonLoader.Utils;

namespace MelonLoader.Bootstrap.Proxy.Android;

public static class AndroidBootstrap
{
    public static string PackageName { get; private set; } = string.Empty;
    public static string DataDir { get; private set; } = string.Empty;
    public static string DotnetDir { get; private set; } = string.Empty;
    public static nint BootstrapHandle { get; private set; }

    [RequiresDynamicCode("Calls Core.Init()")]
    public static int LoadBootstrap()
    {
        StdRedirect.RedirectStdOut();
        StdRedirect.RedirectStdErr();

        EarlyLog.Checkpoint("bootstrap", "Resolving libmain.so handle");
        if (!NativeLibrary.TryLoad("libmain.so", out nint handle))
        {
            if (!string.IsNullOrWhiteSpace(AndroidProxy.NativeLibraryDir))
                NativeLibrary.TryLoad(Path.Combine(AndroidProxy.NativeLibraryDir, "libmain.so"), out handle);
        }
        BootstrapHandle = handle;

        DataDir = GetDataDir();
        EarlyLog.Init(PackageName);
        EarlyLog.Checkpoint("bootstrap", $"Package={PackageName} DataDir={DataDir}");

        EnsurePerms();
        DataDir = EnsureWritableDataDir(DataDir, PackageName);
        Exports.InitializeAndroid(handle);
        EarlyLog.Checkpoint("bootstrap", $"Effective DataDir={DataDir}");

        EarlyLog.Checkpoint("bootstrap", "Initializing APKAssetManager");
        APKAssetManager.Initialize();

        try
        {
            if (APKAssetManager.DoesAssetExist("lemon_stage.txt"))
            {
                byte[] stageBytes = APKAssetManager.GetAssetBytes("lemon_stage.txt");
                EarlyLog.SetStage(Encoding.UTF8.GetString(stageBytes));
            }
            else
            {
                EarlyLog.SetStage("full");
            }
        }
        catch (Exception ex)
        {
            EarlyLog.Checkpoint("bootstrap", $"WARN reading lemon_stage.txt: {ex.Message}");
            EarlyLog.SetStage("full");
        }

        CopyMelonLoaderData(GetApkModificationDate());

        Environment.SetEnvironmentVariable("DOTNET_ROOT", DotnetDir);
        Environment.SetEnvironmentVariable("DOTNET_MULTILEVEL_LOOKUP", "0");
        Environment.SetEnvironmentVariable("SSL_CERT_DIR", "/system/etc/security/cacerts");
        Environment.SetEnvironmentVariable("SSL_CERT_FILE", "/system/etc/security/cacerts");
        EarlyLog.Checkpoint("bootstrap", $"Env set DOTNET_ROOT={DotnetDir} SSL_CERT_DIR=/system/etc/security/cacerts");

        EarlyLog.Checkpoint("bootstrap", "Calling MelonLoader.Bootstrap.Core.Init()");
        Core.Init();
        EarlyLog.Checkpoint("bootstrap", "Core.Init() returned");
        return 1;
    }

    private static void ClearJniException()
    {
        if (JNI.ExceptionCheck())
            JNI.ExceptionClear();
    }

    private static (JObject? Context, bool IsActivity) TryGetContext()
    {
        try
        {
            using JClass unityPlayer = JNI.FindClass("com/unity3d/player/UnityPlayer");
            ClearJniException();
            if (unityPlayer.Valid())
            {
                JFieldID activityFieldId = JNI.GetStaticFieldID(unityPlayer, "currentActivity", "Landroid/app/Activity;");
                ClearJniException();
                if (activityFieldId.Handle != IntPtr.Zero)
                {
                    JObject act = JNI.GetStaticObjectField<JObject>(unityPlayer, activityFieldId);
                    ClearJniException();
                    if (act != null && act.Valid())
                        return (act, true);
                    act?.Dispose();
                }
            }

            using JClass activityThread = JNI.FindClass("android/app/ActivityThread");
            ClearJniException();
            if (activityThread.Valid())
            {
                JMethodID curAppId = JNI.GetStaticMethodID(activityThread, "currentApplication", "()Landroid/app/Application;");
                ClearJniException();
                if (curAppId.Handle != IntPtr.Zero)
                {
                    JObject app = JNI.CallStaticObjectMethod<JObject>(activityThread, curAppId);
                    ClearJniException();
                    if (app != null && app.Valid())
                        return (app, false);
                    app?.Dispose();
                }
            }

            using JClass appGlobals = JNI.FindClass("android/app/AppGlobals");
            ClearJniException();
            if (appGlobals.Valid())
            {
                JMethodID initAppId = JNI.GetStaticMethodID(appGlobals, "getInitialApplication", "()Landroid/app/Application;");
                ClearJniException();
                if (initAppId.Handle != IntPtr.Zero)
                {
                    JObject app = JNI.CallStaticObjectMethod<JObject>(appGlobals, initAppId);
                    ClearJniException();
                    if (app != null && app.Valid())
                        return (app, false);
                    app?.Dispose();
                }
            }
        }
        catch { }
        return (null, false);
    }

    public static string GetDataDir()
    {
        string? pkg = null;
        string extPath = "/sdcard";

        try
        {
            var (ctx, _) = TryGetContext();
            using (ctx)
            {
                if (ctx != null && ctx.Valid())
                {
                    using JClass ctxCls = JNI.GetObjectClass(ctx);
                    ClearJniException();
                    JMethodID getPkgId = JNI.GetMethodID(ctxCls, "getPackageName", "()Ljava/lang/String;");
                    ClearJniException();
                    if (getPkgId.Handle != IntPtr.Zero)
                    {
                        using JString pkgCall = JNI.CallObjectMethod<JString>(ctx, getPkgId);
                        ClearJniException();
                        if (pkgCall != null && pkgCall.Valid())
                            pkg = pkgCall.GetString();
                    }
                }
            }
        }
        catch { }

        if (string.IsNullOrWhiteSpace(pkg))
            pkg = EarlyLog.ReadPackageNameFromCmdline() ?? "com.unity.app";

        PackageName = pkg.Trim();

        try
        {
            using JClass environment = JNI.FindClass("android/os/Environment");
            ClearJniException();
            if (environment.Valid())
            {
                JMethodID getExtId = JNI.GetStaticMethodID(environment, "getExternalStorageDirectory", "()Ljava/io/File;");
                ClearJniException();
                if (getExtId.Handle != IntPtr.Zero)
                {
                    using JObject extObj = JNI.CallStaticObjectMethod<JObject>(environment, getExtId);
                    ClearJniException();
                    if (extObj != null && extObj.Valid())
                    {
                        using JClass fileCls = JNI.GetObjectClass(extObj);
                        ClearJniException();
                        JMethodID toStrId = JNI.GetMethodID(fileCls, "toString", "()Ljava/lang/String;");
                        ClearJniException();
                        if (toStrId.Handle != IntPtr.Zero)
                        {
                            using JString extStr = JNI.CallObjectMethod<JString>(extObj, toStrId);
                            ClearJniException();
                            if (extStr != null && extStr.Valid())
                            {
                                string candidate = extStr.GetString();
                                if (!string.IsNullOrWhiteSpace(candidate))
                                    extPath = candidate;
                            }
                        }
                    }
                }
            }
        }
        catch { }

        return Path.Combine(extPath, "MelonLoader", PackageName);
    }

    private static string EnsureWritableDataDir(string preferredDir, string pkg)
    {
        try
        {
            Directory.CreateDirectory(preferredDir);
            string probe = Path.Combine(preferredDir, ".lemon_write_probe");
            File.WriteAllText(probe, "ok");
            File.Delete(probe);
            return preferredDir;
        }
        catch (Exception ex)
        {
            string fallback = $"/data/data/{pkg}/files/MelonLoader";
            EarlyLog.Checkpoint("bootstrap", $"WARN external DataDir not writable ({ex.Message}); falling back to {fallback}");
            try { Directory.CreateDirectory(fallback); } catch { }
            return fallback;
        }
    }

    public static bool EnsurePerms()
    {
        try
        {
            using JClass buildVersion = JNI.FindClass("android/os/Build$VERSION");
            ClearJniException();
            if (!buildVersion.Valid())
                return true;

            JFieldID sdkIntId = JNI.GetStaticFieldID(buildVersion, "SDK_INT", "I");
            ClearJniException();
            int sdkInt = sdkIntId.Handle != IntPtr.Zero ? JNI.GetStaticField<int>(buildVersion, sdkIntId) : 0;
            ClearJniException();
            if (sdkInt < 30)
                return true;

            const int TRIES = 2;
            const int DELAY = 3000;
            for (int i = 0; i < TRIES; i++)
            {
                using JClass environment = JNI.FindClass("android/os/Environment");
                ClearJniException();
                if (!environment.Valid())
                    return false;

                JMethodID isExtMgrId = environment.GetStaticMethodID("isExternalStorageManager", "()Z");
                ClearJniException();
                if (isExtMgrId.Handle == IntPtr.Zero)
                    return false;

                bool hasPerm = JNI.CallStaticMethod<bool>(environment, isExtMgrId);
                if (JNI.ExceptionCheck())
                {
                    JNI.ExceptionClear();
                    return false;
                }

                if (hasPerm)
                {
                    EarlyLog.Checkpoint("perms", "MANAGE_EXTERNAL_STORAGE already granted");
                    return true;
                }

                var (ctx, isActivity) = TryGetContext();
                using (ctx)
                {
                    if (ctx == null || !ctx.Valid())
                    {
                        EarlyLog.Checkpoint("perms", "WARN no Android Context available for MANAGE_EXTERNAL_STORAGE prompt");
                        return false;
                    }

                    EarlyLog.Checkpoint("perms", $"Requesting MANAGE_EXTERNAL_STORAGE (attempt {i + 1}/{TRIES})");
                    using JClass uri = JNI.FindClass("android/net/Uri");
                    using JClass intent = JNI.FindClass("android/content/Intent");
                    ClearJniException();
                    if (!uri.Valid() || !intent.Valid())
                        return false;

                    using var actionName = JNI.NewString("android.settings.MANAGE_APP_ALL_FILES_ACCESS_PERMISSION");
                    using var packageName = JNI.NewString($"package:{PackageName}");
                    using var uriObj = JNI.CallStaticObjectMethod<JObject>(uri, uri.GetStaticMethodID("parse", "(Ljava/lang/String;)Landroid/net/Uri;"), packageName);
                    ClearJniException();
                    if (uriObj == null || !uriObj.Valid())
                        return false;

                    JMethodID intentConstructor = JNI.GetMethodID(intent, "<init>", "(Ljava/lang/String;Landroid/net/Uri;)V");
                    ClearJniException();
                    if (intentConstructor.Handle == IntPtr.Zero)
                        return false;

                    using var intentObj = JNI.NewObject<JObject>(intent, intentConstructor, actionName, uriObj);
                    ClearJniException();
                    if (intentObj == null || !intentObj.Valid())
                        return false;

                    if (!isActivity)
                    {
                        JMethodID addFlagsId = JNI.GetMethodID(intent, "addFlags", "(I)Landroid/content/Intent;");
                        ClearJniException();
                        if (addFlagsId.Handle != IntPtr.Zero)
                        {
                            using var _ = JNI.CallObjectMethod<JObject>(intentObj, addFlagsId, 0x10000000);
                            ClearJniException();
                        }
                    }

                    using var ctxClass = JNI.GetObjectClass(ctx);
                    ClearJniException();
                    ctxClass.CallVoidMethod(ctx, "startActivity", "(Landroid/content/Intent;)V", intentObj);
                    if (JNI.ExceptionCheck())
                    {
                        JNI.ExceptionClear();
                        EarlyLog.Checkpoint("perms", "WARN startActivity threw JNI exception");
                        return false;
                    }
                }

                Thread.Sleep(DELAY);
            }
        }
        catch (Exception ex)
        {
            EarlyLog.Checkpoint("perms", $"WARN EnsurePerms error: {ex.Message}");
            return false;
        }

        return false;
    }

    public static DateTimeOffset GetApkModificationDate()
    {
        try
        {
            if (!APKAssetManager.DoesAssetExist("lemon_patch_date.txt"))
                return default;
            var assetBytes = APKAssetManager.GetAssetBytes("lemon_patch_date.txt");
            string assetContent = Encoding.UTF8.GetString(assetBytes);
            if (DateTimeOffset.TryParse(assetContent, out DateTimeOffset date))
                return date;
        }
        catch { }
        return default;
    }

    public static void CopyMelonLoaderData(DateTimeOffset date)
    {
        var baseDataDir = DataDir;
        var melonloaderFolder = Path.Combine(baseDataDir, "MelonLoader");
        var baseInternalFolder = $"/data/data/{PackageName}/";
        var dotnetFolder = $"/data/data/{PackageName}/dotnet";

        try
        {
            Directory.CreateDirectory(baseDataDir);
            if (!Directory.Exists(melonloaderFolder) || Directory.GetLastWriteTimeUtc(melonloaderFolder) <= date)
            {
                EarlyLog.Checkpoint("assets_copy", $"Copying assets/MelonLoader -> {baseDataDir}");
                APKAssetManager.SaveItemToDirectory("MelonLoader", baseDataDir, true);
            }
            else
            {
                EarlyLog.Checkpoint("assets_copy", "MelonLoader folder up-to-date");
            }
        }
        catch (Exception ex)
        {
            EarlyLog.Checkpoint("assets_copy", $"ERROR copying MelonLoader assets: {ex}");
        }

        try
        {
            if (!Directory.Exists(dotnetFolder) || Directory.GetLastWriteTimeUtc(dotnetFolder) <= date)
            {
                EarlyLog.Checkpoint("assets_copy", $"Copying assets/dotnet -> {baseInternalFolder}");
                APKAssetManager.SaveItemToDirectory("dotnet", baseInternalFolder, true);
            }
            else
            {
                EarlyLog.Checkpoint("assets_copy", "dotnet folder up-to-date");
            }
        }
        catch (Exception ex)
        {
            EarlyLog.Checkpoint("assets_copy", $"ERROR copying dotnet assets: {ex}");
        }

        DotnetDir = dotnetFolder;
    }
}
#endif
"""
    write_utf8(proxy_dir / "AndroidBootstrap.cs", android_bootstrap_cs)
    print(f"[wrote] {proxy_dir} (EarlyLog, AndroidProxy, AndroidBootstrap, StdRedirect)")


def patch_bootstrap_runtime_files(root: Path) -> None:
    boot = root / "MelonLoader.Bootstrap"

    # 1. Core.cs
    core_path = boot / "Core.cs"
    core_text = core_path.read_text(encoding="utf-8")
    core_text = replace_once(
        core_text,
        "        ModuleSymbolRedirect.Attach();",
        "#if ANDROID\n"
        "        if (Proxy.Android.EarlyLog.Stage == \"minimal_bootstrap\")\n"
        "        {\n"
        "            Proxy.Android.EarlyLog.Checkpoint(\"core\", \"Stage=minimal_bootstrap: skipping ModuleSymbolRedirect.Attach()\");\n"
        "            return;\n"
        "        }\n"
        "#endif\n"
        "        ModuleSymbolRedirect.Attach();",
        "Bootstrap Core.cs minimal_bootstrap stage guard",
    )
    write_utf8(core_path, core_text)

    # 2. Exports.cs
    exports_path = boot / "Exports.cs"
    exports_text = exports_path.read_text(encoding="utf-8")
    exports_text = replace_once(
        exports_text,
        "        return Directory.Exists(DataDir);\n    }",
        "        return Directory.Exists(DataDir);\n    }\n\n"
        "#if ANDROID\n"
        "    internal static void InitializeAndroid(nint moduleHandle)\n"
        "    {\n"
        "        LibraryHandle = moduleHandle;\n"
        "        DataDir = Proxy.Android.AndroidBootstrap.DataDir;\n"
        "        ProcessPath = Environment.ProcessPath ?? string.Empty;\n"
        "        ProcessFileName = Path.GetFileName(ProcessPath);\n"
        "        ProcessFileNameWithoutExt = Path.GetFileNameWithoutExtension(ProcessPath);\n"
        "        ProcessDirectory = Path.GetDirectoryName(ProcessPath) ?? string.Empty;\n"
        "    }\n"
        "#endif",
        "Exports.cs InitializeAndroid",
    )
    exports_text = replace_once(
        exports_text,
        "    [UnmanagedCallersOnly(EntryPoint = \"GetLoaderConfig\")]\n"
        "    public static unsafe void GetLoaderConfig(nint* pConfig)\n"
        "    {\n"
        "        Marshal.StructureToPtr(LoaderConfig.Current, *pConfig, false);\n"
        "    }",
        "    [UnmanagedCallersOnly(EntryPoint = \"GetLoaderConfig\")]\n"
        "    public static unsafe void GetLoaderConfig(nint* pConfig)\n"
        "    {\n"
        "        Marshal.StructureToPtr(LoaderConfig.Current, *pConfig, false);\n"
        "    }\n\n"
        "#if ANDROID\n"
        "    [UnmanagedCallersOnly(EntryPoint = \"GetJavaVM\")]\n"
        "    public static unsafe nint GetJavaVM()\n"
        "    {\n"
        "        return (nint)MelonLoader.Java.JNI.VM;\n"
        "    }\n"
        "#endif",
        "Exports.cs GetJavaVM",
    )
    write_utf8(exports_path, exports_text)

    # 3. LibcNative.cs
    libc_path = boot / "LibcNative.cs"
    libc_text = libc_path.read_text(encoding="utf-8")
    libc_text += """
#if ANDROID
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;

namespace MelonLoader.Bootstrap;

internal partial class LibcNative
{
    internal const int Stdout = 1;
    internal const int Stderr = 2;

    [LibraryImport("libdl.so", EntryPoint = "dlsym", StringMarshalling = StringMarshalling.Utf8)]
    [UnmanagedCallConv(CallConvs = [typeof(CallConvCdecl)])]
    public static partial nint Dlsym(nint handle, string symbol);

    [LibraryImport("libc.so", EntryPoint = "fileno")]
    [UnmanagedCallConv(CallConvs = [typeof(CallConvCdecl)])]
    public static partial int Fileno(nint stream);

    [LibraryImport("libc.so", EntryPoint = "fclose")]
    [UnmanagedCallConv(CallConvs = [typeof(CallConvCdecl)])]
    public static partial int FClose(nint stream);
}
#endif
"""
    write_utf8(libc_path, libc_text)

    # 4. SharedDelegates.cs
    sd_path = boot / "SharedDelegates.cs"
    sd_text = sd_path.read_text(encoding="utf-8")
    sd_text += """
#if ANDROID
[UnmanagedFunctionPointer(CallingConvention.Winapi)]
internal delegate nint GetJavaVMFn();
#endif
"""
    write_utf8(sd_path, sd_text)

    # 5. Utils/PltHook.cs
    plt_path = boot / "Utils/PltHook.cs"
    plt_text = plt_path.read_text(encoding="utf-8")
    plt_text = replace_once(
        plt_text,
        "    internal static readonly string? PlayerFileName = Process.GetCurrentProcess().Modules.OfType<ProcessModule>()\n"
        "        .FirstOrDefault(x => x.FileName.Contains(\"UnityPlayer\"))?.FileName;",
        "#if !ANDROID\n"
        "    internal static readonly string? PlayerFileName = Process.GetCurrentProcess().Modules.OfType<ProcessModule>()\n"
        "        .FirstOrDefault(x => x.FileName.Contains(\"UnityPlayer\"))?.FileName;\n"
        "#else\n"
        "    internal static readonly string? PlayerFileName = FindLibUnityPath();\n"
        "    private static string? FindLibUnityPath()\n"
        "    {\n"
        "        try\n"
        "        {\n"
        "            foreach (string line in File.ReadLines(\"/proc/self/maps\"))\n"
        "            {\n"
        "                if (!line.Contains(\"libunity.so\", StringComparison.Ordinal)) continue;\n"
        "                int slash = line.IndexOf('/');\n"
        "                if (slash >= 0)\n"
        "                    return line[slash..].Trim();\n"
        "            }\n"
        "        }\n"
        "        catch { }\n"
        "        return Process.GetCurrentProcess().Modules.OfType<ProcessModule>()\n"
        "            .FirstOrDefault(x => x.FileName.Contains(\"libunity.so\"))?.FileName;\n"
        "    }\n"
        "#endif",
        "PltHook.cs Android PlayerFileName",
    )
    plt_text = replace_once(
        plt_text,
        "#else\n            pltHookOpened = PlthookOpen(ref pltHook, PlayerFileName) == 0;\n#endif",
        "#elif ANDROID\n"
        "        if (Proxy.Android.AndroidProxy.UnityHandle != IntPtr.Zero)\n"
        "            pltHookOpened = PlthookOpenByHandle(ref pltHook, Proxy.Android.AndroidProxy.UnityHandle) == 0;\n"
        "        if (!pltHookOpened)\n"
        "            pltHookOpened = PlthookOpen(ref pltHook, PlayerFileName) == 0;\n"
        "        Proxy.Android.EarlyLog.Checkpoint(\"plthook\", $\"pltHookOpened={pltHookOpened} target={PlayerFileName ?? \"<handle>\"}\");\n"
        "#else\n"
        "            pltHookOpened = PlthookOpen(ref pltHook, PlayerFileName) == 0;\n"
        "#endif",
        "PltHook.cs Android open by handle",
    )
    write_utf8(plt_path, plt_text)

    # 6. ModuleSymbolRedirect.cs
    msr_path = boot / "ModuleSymbolRedirect.cs"
    msr_text = msr_path.read_text(encoding="utf-8")
    msr_text = replace_once(
        msr_text,
        "        private static bool _runtimeInitialised;\n\n#if LINUX || OSX\n        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]\n#endif",
        "        private static bool _runtimeInitialised;\n        private static readonly object _initLock = new();\n\n#if LINUX || OSX || ANDROID\n        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]\n#endif",
        "ModuleSymbolRedirect.cs DetourFn ANDROID + initLock",
    )
    msr_text = replace_once(
        msr_text,
        "#if LINUX || OSX\n            PltHook.InstallHooks\n            ([\n                (\"dlsym\", detourPtr)\n            ]);\n#endif",
        "#if LINUX || OSX || ANDROID\n            PltHook.InstallHooks\n            ([\n                (\"dlsym\", detourPtr)\n            ]);\n#endif",
        "ModuleSymbolRedirect.cs Attach ANDROID",
    )
    msr_text = replace_once(
        msr_text,
        "#elif LINUX || OSX\n            return dlsym(handle, symbolName);",
        "#elif LINUX || OSX || ANDROID\n            return dlsym(handle, symbolName);",
        "ModuleSymbolRedirect.cs GetSymbol ANDROID",
    )
    msr_text = replace_once(
        msr_text,
        "            if (!MonoHandler.SymbolRedirects.TryGetValue(symbolName, out var redirect)\n"
        "                && !Il2CppHandler.SymbolRedirects.TryGetValue(symbolName, out redirect))\n"
        "                return originalSymbolAddress;\n\n"
        "            if (!_runtimeInitialised)\n"
        "            {\n"
        "                _runtimeInitialised = true;\n"
        "                MelonDebug.Log(\"Initializing Runtime\");\n"
        "                redirect.InitMethod(handle);\n"
        "                if (!LoaderConfig.Current.Loader.CapturePlayerLogs)\n"
        "                    ConsoleHandler.ResetHandles();\n"
        "            }",
        "            if (!MonoHandler.SymbolRedirects.TryGetValue(symbolName, out var redirect)\n"
        "                && !Il2CppHandler.SymbolRedirects.TryGetValue(symbolName, out redirect))\n"
        "                return originalSymbolAddress;\n\n"
        "#if ANDROID\n"
        "            if (symbolName == \"il2cpp_runtime_invoke\" && Proxy.Android.EarlyLog.Stage == \"il2cpp_init_only\")\n"
        "            {\n"
        "                Proxy.Android.EarlyLog.Checkpoint(\"dlsym\", \"Stage=il2cpp_init_only: skipping il2cpp_runtime_invoke redirect\");\n"
        "                return originalSymbolAddress;\n"
        "            }\n"
        "            Proxy.Android.EarlyLog.Checkpoint(\"dlsym\", $\"Redirecting {symbolName} original=0x{originalSymbolAddress:X}\");\n"
        "#endif\n\n"
        "            lock (_initLock)\n"
        "            {\n"
        "                if (!_runtimeInitialised)\n"
        "                {\n"
        "                    _runtimeInitialised = true;\n"
        "                    MelonDebug.Log(\"Initializing Runtime\");\n"
        "                    redirect.InitMethod(handle);\n"
        "                    if (!LoaderConfig.Current.Loader.CapturePlayerLogs)\n"
        "                        ConsoleHandler.ResetHandles();\n"
        "                }\n"
        "            }",
        "ModuleSymbolRedirect.cs SymbolDetour ANDROID",
    )
    msr_text = replace_once(
        msr_text,
        "#elif LINUX\n        [DllImport(\"libdl.so.2\")]\n        private static extern IntPtr dlsym(nint handle, nint symbol);",
        "#elif ANDROID\n        [DllImport(\"libdl.so\")]\n        private static extern IntPtr dlsym(nint handle, nint symbol);\n"
        "#elif LINUX\n        [DllImport(\"libdl.so.2\")]\n        private static extern IntPtr dlsym(nint handle, nint symbol);",
        "ModuleSymbolRedirect.cs dlsym P/Invoke ANDROID",
    )
    write_utf8(msr_path, msr_text)

    # 7. Logging/ConsoleHandler.cs & Logging/MelonLogger.cs
    ch_path = boot / "Logging/ConsoleHandler.cs"
    ch_text = ch_path.read_text(encoding="utf-8")
    ch_text = replace_once(
        ch_text,
        "        if (consoleWindow != 0)\n#endif\n            if (!string.IsNullOrEmpty(title))\n                Console.Title = title;",
        "        if (consoleWindow != 0)\n#endif\n#if !ANDROID\n            if (!string.IsNullOrEmpty(title))\n                Console.Title = title;\n#endif",
        "ConsoleHandler.cs guard Console.Title on Android",
    )
    write_utf8(ch_path, ch_text)

    ml_path = boot / "Logging/MelonLogger.cs"
    ml_text = ml_path.read_text(encoding="utf-8")
    ml_text = replace_once(
        ml_text,
        "            ConsoleHandler.OpenConsole(LoaderConfig.Current.Console.AlwaysOnTop, title);",
        "            ConsoleHandler.OpenConsole(LoaderConfig.Current.Console.AlwaysOnTop, title);",
        "MelonLogger.cs OpenConsole check",
    )
    ml_text = replace_once(
        ml_text,
        "        Console.WriteLine($\"[{time.Pastel(timeColor)}] {msg.Pastel(msgColor)}\");",
        "#if ANDROID\n        Proxy.Android.AndroidProxy.Log($\"[{time}] {strippedMessage}\");\n#else\n        Console.WriteLine($\"[{time.Pastel(timeColor)}] {msg.Pastel(msgColor)}\");\n#endif",
        "MelonLogger.cs Log1 Android",
    )
    ml_text = replace_once(
        ml_text,
        "        if (!sectionName.IsEmpty)\n"
        "            Console.WriteLine($\"[{time.Pastel(timeColor)}] [{sectionName.Pastel(sectionColor)}] {msg.Pastel(msgColor)}\");\n"
        "        else\n"
        "            Console.WriteLine($\"[{time.Pastel(timeColor)}] {msg.Pastel(msgColor)}\");",
        "#if ANDROID\n"
        "        Proxy.Android.AndroidProxy.Log($\"[{time}] {sectionPart}{strippedMessage}\");\n"
        "#else\n"
        "        if (!sectionName.IsEmpty)\n"
        "            Console.WriteLine($\"[{time.Pastel(timeColor)}] [{sectionName.Pastel(sectionColor)}] {msg.Pastel(msgColor)}\");\n"
        "        else\n"
        "            Console.WriteLine($\"[{time.Pastel(timeColor)}] {msg.Pastel(msgColor)}\");\n"
        "#endif",
        "MelonLogger.cs Log2 Android",
    )
    ml_text = replace_once(
        ml_text,
        "        Console.WriteLine(logStr.Pastel(color));",
        "#if ANDROID\n        Proxy.Android.AndroidProxy.Log(logStr);\n#else\n        Console.WriteLine(logStr.Pastel(color));\n#endif",
        "MelonLogger.cs LogInternal Android",
    )
    ml_text = replace_once(
        ml_text,
        "        Console.WriteLine($\"[{time.Pastel(timeColor)}] {name.Pastel(nameColor)} {info}\");",
        "#if ANDROID\n        Proxy.Android.AndroidProxy.Log($\"[{time}] {name} {info}\");\n#else\n        Console.WriteLine($\"[{time.Pastel(timeColor)}] {name.Pastel(nameColor)} {info}\");\n#endif",
        "MelonLogger.cs LogMelonInfo Android",
    )
    ml_text = replace_once(
        ml_text,
        "        Console.WriteLine();\n    }\n}",
        "#if ANDROID\n        Proxy.Android.AndroidProxy.Log(\"\");\n#else\n        Console.WriteLine();\n#endif\n    }\n}",
        "MelonLogger.cs LogSpacer Android",
    )
    write_utf8(ml_path, ml_text)

    # 8. RuntimeHandlers/Il2Cpp/Il2CppLib.cs -> add GetAllAttachedThreads & Il2CppThread structs on Android
    il2cpp_lib_path = boot / "RuntimeHandlers/Il2Cpp/Il2CppLib.cs"
    il2cpp_lib_text = il2cpp_lib_path.read_text(encoding="utf-8")
    if "GetAllAttachedThreads" not in il2cpp_lib_text:
        il2cpp_lib_text = replace_once(
            il2cpp_lib_text,
            "    public required RuntimeInvokeFn RuntimeInvoke { get; init; }",
            "    public required RuntimeInvokeFn RuntimeInvoke { get; init; }\n"
            "#if ANDROID\n"
            "    public GetAllAttachedThreadsFn? GetAllAttachedThreads { get; init; }\n"
            "#endif",
            "Il2CppLib.cs GetAllAttachedThreads property",
        )
        il2cpp_lib_text = replace_once(
            il2cpp_lib_text,
            "        if (!NativeFunc.GetExport<InitFn>(hRuntime, \"il2cpp_init\", out var init)\n"
            "            || !NativeFunc.GetExport<RuntimeInvokeFn>(hRuntime, \"il2cpp_runtime_invoke\", out var runtimeInvoke)\n"
            "            || !NativeFunc.GetExport<MethodGetNameFn>(hRuntime, \"il2cpp_method_get_name\", out var methodGetName))\n"
            "            return null;",
            "        if (!NativeFunc.GetExport<InitFn>(hRuntime, \"il2cpp_init\", out var init)\n"
            "            || !NativeFunc.GetExport<RuntimeInvokeFn>(hRuntime, \"il2cpp_runtime_invoke\", out var runtimeInvoke)\n"
            "            || !NativeFunc.GetExport<MethodGetNameFn>(hRuntime, \"il2cpp_method_get_name\", out var methodGetName))\n"
            "            return null;\n"
            "#if ANDROID\n"
            "        NativeFunc.GetExport<GetAllAttachedThreadsFn>(hRuntime, \"il2cpp_thread_get_all_attached_threads\", out var getAllAttachedThreads);\n"
            "#endif",
            "Il2CppLib.cs load il2cpp_thread_get_all_attached_threads",
        )
        il2cpp_lib_text = replace_once(
            il2cpp_lib_text,
            "            Init = init,\n            RuntimeInvoke = runtimeInvoke\n        };",
            "            Init = init,\n            RuntimeInvoke = runtimeInvoke,\n"
            "#if ANDROID\n"
            "            GetAllAttachedThreads = getAllAttachedThreads,\n"
            "#endif\n"
            "        };",
            "Il2CppLib.cs assign GetAllAttachedThreads",
        )
        il2cpp_lib_text = replace_once(
            il2cpp_lib_text,
            "    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]\n    public delegate nint MethodGetNameFn(nint method);",
            "    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]\n"
            "    public delegate nint MethodGetNameFn(nint method);\n\n"
            "#if ANDROID\n"
            "    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]\n"
            "    public delegate nint GetAllAttachedThreadsFn(ref nuint size);\n"
            "#endif",
            "Il2CppLib.cs GetAllAttachedThreadsFn delegate",
        )
        il2cpp_lib_text += """
#if ANDROID
[StructLayout(LayoutKind.Sequential)]
internal struct Il2CppThread
{
    public Il2CppObject obj;
    public IntPtr internal_thread;
}

[StructLayout(LayoutKind.Sequential)]
internal struct Il2CppObject
{
    public IntPtr klass;
    public IntPtr monitor;
}

[StructLayout(LayoutKind.Sequential)]
internal struct Il2CppInternalThread
{
    public Il2CppObject obj;
    public int lock_thread_id;
    public IntPtr handle;
    public IntPtr native_handle;
    public IntPtr name_chars;
    public uint name_len;
    public uint state;
    public IntPtr abort_exc;
    public int abort_state_handle;
    public ulong tid;
}
#endif
"""
        write_utf8(il2cpp_lib_path, il2cpp_lib_text)

    # 9. RuntimeHandlers/Il2Cpp/Il2CppHandler.cs
    il2cpp_handler_cs = """using MelonLoader.Bootstrap.Utils;
using System;
using System.Runtime.InteropServices;
using MelonLoader.Bootstrap.RuntimeHandlers.Dotnet;

namespace MelonLoader.Bootstrap.RuntimeHandlers.Il2Cpp;

internal static class Il2CppHandler
{
    private static Il2CppLib il2cpp = null!;
#if ANDROID
    private static ClrMonoLib? mono;
    private static readonly ClrMonoLib.CheckThreadDelegate MonoCheckThreadDel = MonoCheckThread;
    private static readonly ClrMonoLib.MonoUnhandledExceptionDelegate MonoUnhandledExceptionDel = MonoUnhandledExceptionHandler;
#endif
    private static bool il2cppInitDone;
    private static bool invokeStarted;

    private static readonly Il2CppLib.InitFn Il2CPPInitDetourFn = InitDetour;
    private static readonly Il2CppLib.RuntimeInvokeFn InvokeDetourFn = InvokeDetour;
    internal static readonly Dictionary<string, (Action<nint> InitMethod, IntPtr detourPtr)> SymbolRedirects = new()
    {
        { "il2cpp_init", (Initialize, Marshal.GetFunctionPointerForDelegate(Il2CPPInitDetourFn))},
        { "il2cpp_runtime_invoke", (Initialize, Marshal.GetFunctionPointerForDelegate(InvokeDetourFn))},
    };

    public static void Initialize(nint handle)
    {
#if ANDROID
        Proxy.Android.EarlyLog.Checkpoint("il2cpp", $"Il2CppHandler.Initialize handle=0x{handle:X}");
#endif
        var il2cppLib = Il2CppLib.TryLoad(handle);
        if (il2cppLib is null)
        {
            Core.Logger.Error("Could not load il2cpp");
#if ANDROID
            Proxy.Android.EarlyLog.Checkpoint("il2cpp", "ERROR Could not load il2cpp exports");
#endif
            return;
        }

        il2cpp = il2cppLib;
    }

    internal static nint InitDetour(nint a)
    {
        if (il2cppInitDone)
            return il2cpp.Init(a);

        ConsoleHandler.ResetHandles();
        MelonDebug.Log("In init detour");
#if ANDROID
        Proxy.Android.EarlyLog.Checkpoint("il2cpp_init", "BEFORE original il2cpp_init");
#endif

        var domain = il2cpp.Init(a);

#if ANDROID
        Proxy.Android.EarlyLog.Checkpoint("il2cpp_init", $"AFTER original il2cpp_init domain=0x{domain:X}");
#endif

        DotnetHandler.Initialize();
        il2cppInitDone = true;

        return domain;
    }

    internal static nint InvokeDetour(nint method, nint obj, nint args, nint exc)
    {
        var result = il2cpp.RuntimeInvoke(method, obj, args, exc);
        if (invokeStarted)
            return result;

        var name = il2cpp.GetMethodName(method);
        if (name == null || !name.Contains("Internal_ActiveSceneChanged"))
            return result;

        invokeStarted = true;
        MelonDebug.Log("Invoke hijacked");

#if ANDROID
        Proxy.Android.EarlyLog.Checkpoint("il2cpp_invoke", $"Hit {name}");
        if (Proxy.Android.EarlyLog.Stage == "no_hooks")
        {
            Proxy.Android.EarlyLog.Checkpoint("il2cpp_invoke", "Stage=no_hooks: skipping DotnetHandler.Start()");
            return result;
        }
        mono?.ThreadSuspendReload();
        Proxy.Android.EarlyLog.Checkpoint("il2cpp_invoke", "Mono thread reset; BEFORE DotnetHandler.Start()");
#endif

        DotnetHandler.Start();

#if ANDROID
        Proxy.Android.EarlyLog.Checkpoint("il2cpp_invoke", "AFTER DotnetHandler.Start()");
#endif

        return result;
    }

#if ANDROID
    internal static void ApplyMonoPatches()
    {
        if (mono == null)
        {
            mono = ClrMonoLib.TryLoad();
            if (mono == null)
            {
                Proxy.Android.EarlyLog.Checkpoint("clr_mono", "WARN ClrMonoLib.TryLoad() returned null");
                Core.Logger.Error("Could not load CoreCLR Mono exports");
                return;
            }
        }

        Proxy.Android.EarlyLog.Checkpoint("clr_mono", "Applying CoreCLR Mono runtime patches");
        mono.SetLevelString("warning");
        mono.SetMaskString("all");
        mono.InstallUnhandledExceptionHook(MonoUnhandledExceptionDel, IntPtr.Zero);
        mono.SetThreadChecker(MonoCheckThreadDel);
        Proxy.Android.EarlyLog.Checkpoint("clr_mono", "CoreCLR Mono runtime patches installed");
    }

    private static void MonoUnhandledExceptionHandler(IntPtr exc, IntPtr userData)
    {
        if (exc == IntPtr.Zero)
            return;
        Proxy.Android.EarlyLog.Checkpoint("clr_mono", $"Unhandled exception ptr=0x{exc:X}");
        mono?.PrintUnhandledException(exc);
    }

    private static bool MonoCheckThread(ulong threadId)
    {
        if (il2cpp?.GetAllAttachedThreads == null)
            return true;

        nuint size = 0;
        IntPtr threads = il2cpp.GetAllAttachedThreads(ref size);
        if (threads == IntPtr.Zero || size == 0)
            return true;

        int count = (int)size;
        IntPtr[] threadsSlice = new IntPtr[count];
        Marshal.Copy(threads, threadsSlice, 0, count);

        for (int i = 0; i < threadsSlice.Length; i++)
        {
            if (threadsSlice[i] == IntPtr.Zero)
                continue;
            Il2CppThread thread = Marshal.PtrToStructure<Il2CppThread>(threadsSlice[i]);
            if (thread.internal_thread == IntPtr.Zero)
                continue;
            Il2CppInternalThread internalThread = Marshal.PtrToStructure<Il2CppInternalThread>(thread.internal_thread);
            if (internalThread.tid == threadId)
                return false;
        }

        return true;
    }
#endif
}
"""
    write_utf8(boot / "RuntimeHandlers/Il2Cpp/Il2CppHandler.cs", il2cpp_handler_cs)

    # 10. RuntimeHandlers/Dotnet/DotnetHandler.cs & DotnetLib.cs
    dh_path = boot / "RuntimeHandlers/Dotnet/DotnetHandler.cs"
    dh_text = dh_path.read_text(encoding="utf-8")
    dh_text = replace_once(
        dh_text,
        "        // Try to use a portable runtime from game directory",
        "#if ANDROID\n"
        "        string androidDotnetDir = Proxy.Android.AndroidBootstrap.DotnetDir;\n"
        "        Proxy.Android.EarlyLog.Checkpoint(\"dotnet\", $\"Scanning Android dotnetDir={androidDotnetDir} runtimeConfig={runtimeConfigPath}\");\n"
        "        if (ScanDirectory(androidDotnetDir) && InitializeDomain(runtimeConfigPath, nativeHostPath))\n"
        "            return;\n"
        "        Proxy.Android.EarlyLog.Checkpoint(\"dotnet\", \"ERROR Android ScanDirectory/InitializeDomain failed\");\n"
        "#endif\n\n"
        "        // Try to use a portable runtime from game directory",
        "DotnetHandler.cs Android DotnetDir scan",
    )
    dh_text = replace_once(
        dh_text,
        "        var startFuncPtr = Exports.LibraryHandle;\n\n"
        "        MelonDebug.Log(\"Invoking NativeHost entry\");\n"
        "        initialize(ref startFuncPtr);",
        "        var startFuncPtr = Exports.LibraryHandle;\n\n"
        "#if ANDROID\n"
        "        Il2Cpp.Il2CppHandler.ApplyMonoPatches();\n"
        "        Proxy.Android.EarlyLog.Checkpoint(\"dotnet\", $\"BEFORE NativeEntry bootstrapHandle=0x{startFuncPtr:X}\");\n"
        "#endif\n"
        "        MelonDebug.Log(\"Invoking NativeHost entry\");\n"
        "        initialize(ref startFuncPtr);\n"
        "#if ANDROID\n"
        "        Proxy.Android.EarlyLog.Checkpoint(\"dotnet\", $\"AFTER NativeEntry startFuncPtr=0x{startFuncPtr:X}\");\n"
        "#endif",
        "DotnetHandler.cs ApplyMonoPatches + NativeEntry checkpoints",
    )
    write_utf8(dh_path, dh_text)

    dl_path = boot / "RuntimeHandlers/Dotnet/DotnetLib.cs"
    dl_text = dl_path.read_text(encoding="utf-8")
    dl_text = replace_once(
        dl_text,
        "        if (status != 0)\n        {\n            context = status;\n            return false;\n        }",
        "        if (status != 0)\n        {\n"
        "#if ANDROID\n"
        "            Proxy.Android.EarlyLog.Checkpoint(\"dotnet\", $\"ERROR hostfxr_initialize_for_runtime_config status=0x{status:X}\");\n"
        "#endif\n"
        "            context = status;\n            return false;\n        }",
        "DotnetLib.cs log hostfxr error code",
    )
    write_utf8(dl_path, dl_text)

    # 11. RuntimeHandlers/Mono/MonoHandler.cs (just ensure LINUX || OSX || ANDROID on dlsym)
    mh_path = boot / "RuntimeHandlers/Mono/MonoHandler.cs"
    mh_text = mh_path.read_text(encoding="utf-8")
    mh_text = mh_text.replace("#elif LINUX || OSX", "#elif LINUX || OSX || ANDROID")
    write_utf8(mh_path, mh_text)

    print("[patched] MelonLoader.Bootstrap core, PltHook, ModuleSymbolRedirect, Logger, Il2CppHandler, DotnetHandler")


def patch_managed_melonloader(root: Path) -> None:
    ml = root / "MelonLoader"

    # 1. MelonLoader.csproj: support AndroidBuild (net8.0 + JNI + APKAssetManager)
    csproj_path = ml / "MelonLoader.csproj"
    csproj_text = csproj_path.read_text(encoding="utf-8")
    csproj_text = replace_once(
        csproj_text,
        "        <TargetFrameworks>$(MonoTarget);$(MonoBleedingEdgeTarget);$(DotNetTarget)</TargetFrameworks>",
        "        <TargetFrameworks>$(MonoTarget);$(MonoBleedingEdgeTarget);$(DotNetTarget)</TargetFrameworks>\n"
        "        <TargetFrameworks Condition=\"'$(AndroidBuild)' == 'true'\">$(DotNetTarget)</TargetFrameworks>\n"
        "        <CopyLocalLockFileAssemblies Condition=\"'$(AndroidBuild)' == 'true'\">true</CopyLocalLockFileAssemblies>\n"
        "        <GenerateRuntimeConfigurationFiles Condition=\"'$(AndroidBuild)' == 'true'\">true</GenerateRuntimeConfigurationFiles>",
        "MelonLoader.csproj AndroidBuild TargetFrameworks + CopyLocalLockFileAssemblies",
    )
    csproj_text = replace_once(
        csproj_text,
        "    <ItemGroup>\n        <Compile Include=\"$(SolutionDir)/MelonLoader.Bootstrap/Logging/ColorRGB.cs\"",
        "    <ItemGroup Condition=\"'$(AndroidBuild)' != 'true'\">\n"
        "        <Compile Remove=\"JNI\\**\\*.cs\" />\n"
        "        <Compile Remove=\"Utils\\APKAssetManager.cs\" />\n"
        "    </ItemGroup>\n\n"
        "    <ItemGroup>\n        <Compile Include=\"$(SolutionDir)/MelonLoader.Bootstrap/Logging/ColorRGB.cs\"",
        "MelonLoader.csproj exclude JNI on desktop",
    ) if "ColorRGB.cs" in csproj_text else replace_once(
        csproj_text,
        "    <ItemGroup>\n        <Compile Include=\"$(SolutionDir)/MelonLoader.Bootstrap/Logging/ColorARGB.cs\"",
        "    <ItemGroup Condition=\"'$(AndroidBuild)' != 'true'\">\n"
        "        <Compile Remove=\"JNI\\**\\*.cs\" />\n"
        "        <Compile Remove=\"Utils\\APKAssetManager.cs\" />\n"
        "    </ItemGroup>\n\n"
        "    <ItemGroup>\n        <Compile Include=\"$(SolutionDir)/MelonLoader.Bootstrap/Logging/ColorARGB.cs\"",
        "MelonLoader.csproj exclude JNI on desktop (ColorARGB)",
    )
    write_utf8(csproj_path, csproj_text)

    # 2. LoaderConfig.cs
    lc_path = ml / "LoaderConfig.cs"
    lc_text = lc_path.read_text(encoding="utf-8")
    lc_text = replace_once(
        lc_text,
        "        var baseDir = Path.GetDirectoryName(Environment.ProcessPath)!;\n\n#if OSX\n        baseDir = GetParentDirectory(baseDir, 3);\n#endif",
        "#if ANDROID\n"
        "        var baseDir = MelonLoader.Bootstrap.Proxy.Android.AndroidBootstrap.DataDir;\n"
        "#else\n"
        "        var baseDir = Path.GetDirectoryName(Environment.ProcessPath)!;\n\n"
        "#if OSX\n"
        "        baseDir = GetParentDirectory(baseDir, 3);\n"
        "#endif\n"
        "#endif",
        "LoaderConfig.cs Android DataDir",
    )
    lc_text = replace_once(
        lc_text,
        "        var userDataFolder = Path.Combine(baseDir, \"UserData\");\n"
        "        if (!Directory.Exists(userDataFolder))\n"
        "            Directory.CreateDirectory(userDataFolder);",
        "        var userDataFolder = Path.Combine(baseDir, \"UserData\");\n"
        "        try\n"
        "        {\n"
        "            if (!Directory.Exists(userDataFolder))\n"
        "                Directory.CreateDirectory(userDataFolder);\n"
        "        }\n"
        "        catch { }",
        "LoaderConfig.cs guard CreateDirectory",
    )
    lc_text = replace_once(
        lc_text,
        "        private const string MonoPathSeparatorDescription =\n#if WINDOWS\n            \"semicolon (;)\";\n#elif LINUX || OSX\n            \"colon (:)\";\n#endif",
        "        private const string MonoPathSeparatorDescription =\n#if WINDOWS\n            \"semicolon (;)\";\n#elif LINUX || OSX || ANDROID\n            \"colon (:)\";\n#endif",
        "LoaderConfig.cs MonoPathSeparatorDescription Android",
    )
    write_utf8(lc_path, lc_text)

    # 3. InternalUtils/BootstrapLibrary.cs
    bl_path = ml / "InternalUtils/BootstrapLibrary.cs"
    bl_text = bl_path.read_text(encoding="utf-8")
    bl_text = replace_once(
        bl_text,
        "    internal GetLoaderConfigFn GetLoaderConfig { get; private set; }",
        "    internal GetLoaderConfigFn GetLoaderConfig { get; private set; }\n"
        "#if ANDROID\n"
        "    internal GetJavaVMFn GetJavaVM { get; private set; }\n"
        "#endif",
        "BootstrapLibrary.cs GetJavaVM",
    )
    write_utf8(bl_path, bl_text)

    # 4. Core.cs
    core_path = ml / "Core.cs"
    core_text = core_path.read_text(encoding="utf-8")
    core_text = replace_once(
        core_text,
        "            MelonLaunchOptions.Load();",
        "            MelonLaunchOptions.Load();\n\n"
        "#if ANDROID\n"
        "            Java.JNI.Initialize(BootstrapInterop.Library.GetJavaVM());\n"
        "            APKAssetManager.Initialize();\n"
        "#endif",
        "MelonLoader/Core.cs JNI + APKAssetManager init",
    )
    core_text = replace_once(
        core_text,
        "            Fixes.Dotnet.DotnetAssemblyLoadContextFix.Install();",
        "#if !ANDROID\n"
        "            Fixes.Dotnet.DotnetAssemblyLoadContextFix.Install();\n"
        "#endif",
        "MelonLoader/Core.cs skip DotnetAssemblyLoadContextFix on Android",
    )
    write_utf8(core_path, core_text)

    alc_fix_path = ml / "Fixes" / "Dotnet" / "DotnetAssemblyLoadContextFix.cs"
    alc_fix_text = alc_fix_path.read_text(encoding="utf-8")
    alc_fix_text = replace_once(
        alc_fix_text,
        "#if NET6_0_OR_GREATER",
        "#if NET6_0_OR_GREATER && !ANDROID",
        "MelonLoader/Fixes/Dotnet/DotnetAssemblyLoadContextFix.cs disable on Android",
    )
    write_utf8(alc_fix_path, alc_fix_text)

    # 5. MelonUtils.cs
    mu_path = ml / "MelonUtils.cs"
    mu_text = mu_path.read_text(encoding="utf-8")
    mu_text = replace_once(
        mu_text,
        "            if (IsWindows)\n"
        "                CurrentPlatform = IsGame32Bit() ? MelonPlatformAttribute.CompatiblePlatforms.WINDOWS_X86 : MelonPlatformAttribute.CompatiblePlatforms.WINDOWS_X64;\n"
        "            if (IsUnix)\n"
        "                CurrentPlatform = MelonPlatformAttribute.CompatiblePlatforms.LINUX;\n"
        "            if (IsMac)\n"
        "                CurrentPlatform = MelonPlatformAttribute.CompatiblePlatforms.MAC;",
        "#if ANDROID\n"
        "            CurrentPlatform = MelonPlatformAttribute.CompatiblePlatforms.ANDROID;\n"
        "#else\n"
        "            if (IsWindows)\n"
        "                CurrentPlatform = IsGame32Bit() ? MelonPlatformAttribute.CompatiblePlatforms.WINDOWS_X86 : MelonPlatformAttribute.CompatiblePlatforms.WINDOWS_X64;\n"
        "            if (IsUnix)\n"
        "                CurrentPlatform = MelonPlatformAttribute.CompatiblePlatforms.LINUX;\n"
        "            if (IsMac)\n"
        "                CurrentPlatform = MelonPlatformAttribute.CompatiblePlatforms.MAC;\n"
        "#endif",
        "MelonUtils.cs CurrentPlatform ANDROID",
    )
    mu_text = replace_once(
        mu_text,
        "        public static bool IsMac => GetPlatform is PlatformID.MacOSX;",
        "        public static bool IsMac => GetPlatform is PlatformID.MacOSX;\n"
        "        public static bool IsAndroid =>\n"
        "#if ANDROID\n"
        "            true;\n"
        "#else\n"
        "            false;\n"
        "#endif",
        "MelonUtils.cs IsAndroid",
    )
    write_utf8(mu_path, mu_text)

    # 6. Utils/MelonEnvironment.cs
    me_path = ml / "Utils/MelonEnvironment.cs"
    me_text = me_path.read_text(encoding="utf-8")
    me_text = replace_once(
        me_text,
        "#if !NET6_0\n            \"net35\";\n#else\n            \"net6\";\n#endif",
        "#if !NET6_0_OR_GREATER\n            \"net35\";\n#else\n            \"net6\";\n#endif",
        "MelonEnvironment.cs OurRuntimeName NET6_0_OR_GREATER",
    )
    me_text = replace_once(
        me_text,
        "        public static string GameExecutablePath { get; } =\n#if OSX\n            MelonUtils.GetPathAncestor(Process.GetCurrentProcess()!.MainModule!.FileName, 3);\n#else\n            Process.GetCurrentProcess().MainModule.FileName;\n#endif\n        public static string MelonLoaderDirectory { get; } = Path.Combine(MelonBaseDirectory, \"MelonLoader\");\n        public static string GameRootDirectory { get; } = Path.GetDirectoryName(GameExecutablePath);",
        "        public static string GameExecutablePath { get; } =\n#if OSX\n            MelonUtils.GetPathAncestor(Process.GetCurrentProcess()!.MainModule!.FileName, 3);\n#elif ANDROID\n            Process.GetCurrentProcess().MainModule?.FileName ?? Path.Combine(MelonBaseDirectory, Path.GetFileName(MelonBaseDirectory));\n#else\n            Process.GetCurrentProcess().MainModule.FileName;\n#endif\n        public static string MelonLoaderDirectory { get; } = Path.Combine(MelonBaseDirectory, \"MelonLoader\");\n#if !ANDROID\n        public static string GameRootDirectory { get; } = Path.GetDirectoryName(GameExecutablePath);\n#else\n        public static string GameRootDirectory { get; } = MelonBaseDirectory;\n        public static string PackageName { get; } = Path.GetFileName(MelonBaseDirectory);\n#endif",
        "MelonEnvironment.cs Android GameRootDirectory + PackageName",
    )
    me_text = replace_once(
        me_text,
        "        public static string UnityGameDataDirectory { get; } = \n#if OSX\n            Path.Combine(GameExecutablePath!, \"Contents/Resources/Data\");\n#else\n            Path.Combine(GameRootDirectory, GameExecutableName + \"_Data\");\n#endif",
        "        public static string UnityGameDataDirectory { get; } = \n#if OSX\n            Path.Combine(GameExecutablePath!, \"Contents/Resources/Data\");\n#elif ANDROID\n            \"bin/Data/\";\n#else\n            Path.Combine(GameRootDirectory, GameExecutableName + \"_Data\");\n#endif",
        "MelonEnvironment.cs Android UnityGameDataDirectory",
    )
    write_utf8(me_path, me_text)

    # 7. Utils/NativeLibrary.cs
    nl_path = ml / "Utils/NativeLibrary.cs"
    nl_text = nl_path.read_text(encoding="utf-8")
    nl_text = replace_once(
        nl_text,
        "using System.Runtime.InteropServices;",
        "using System.Runtime.InteropServices;\nusing MelonLoader.Utils;",
        "NativeLibrary.cs using MelonLoader.Utils",
    )
    nl_text = replace_once(
        nl_text,
        "            if (ptr == IntPtr.Zero)\n                throw new Exception($\"Unable to Load Native Library {filepath}!\");",
        "#if !ANDROID\n"
        "            if (ptr == IntPtr.Zero)\n"
        "                throw new Exception($\"Unable to Load Native Library {filepath}!\");\n"
        "#else\n"
        "            if (ptr == IntPtr.Zero)\n"
        "            {\n"
        "                var error = Marshal.PtrToStringAnsi(dlerror());\n"
        "                throw new DlErrorException($\"Unable to Load Native Library {filepath}!\\ndlerror: {error}\");\n"
        "            }\n"
        "#endif",
        "NativeLibrary.cs DlErrorException",
    )
    nl_text = replace_once(
        nl_text,
        "#elif OSX\n            if (!Path.HasExtension(name))\n                name += \".dylib\";\n\n            return dlopen(name, RTLD_NOW);\n#endif",
        "#elif OSX\n"
        "            if (!Path.HasExtension(name))\n"
        "                name += \".dylib\";\n\n"
        "            return dlopen(name, RTLD_NOW);\n"
        "#elif ANDROID\n"
        "            string path = name;\n"
        "            if (File.Exists(path))\n"
        "            {\n"
        "                string fileName = Path.GetFileName(path);\n"
        "                string destDir = Path.Combine(\"/data/data\", MelonEnvironment.PackageName);\n"
        "                Directory.CreateDirectory(destDir);\n"
        "                path = Path.Combine(destDir, fileName);\n"
        "                FileInfo newLib = new(name);\n"
        "                FileInfo copiedLib = new(path);\n"
        "                if (!copiedLib.Exists || newLib.LastWriteTimeUtc > copiedLib.LastWriteTimeUtc)\n"
        "                    File.Copy(name, path, true);\n"
        "            }\n"
        "            return dlopen(path, RTLD_NOW);\n"
        "#endif",
        "NativeLibrary.cs AgnosticLoadLibrary ANDROID",
    )
    nl_text = replace_once(
        nl_text,
        "#elif LINUX || OSX\n            return dlsym(hModule, lpProcName);",
        "#elif LINUX || OSX || ANDROID\n            return dlsym(hModule, lpProcName);",
        "NativeLibrary.cs AgnosticGetProcAddress ANDROID",
    )
    nl_text = replace_once(
        nl_text,
        "#elif LINUX\n        [DllImport(\"libdl.so.2\")]",
        "#elif ANDROID\n"
        "        [DllImport(\"libdl.so\")]\n"
        "        protected static extern IntPtr dlopen(string filename, int flags);\n\n"
        "        [DllImport(\"libdl.so\")]\n"
        "        protected static extern IntPtr dlsym(IntPtr handle, string symbol);\n\n"
        "        [DllImport(\"libdl.so\")]\n"
        "        protected static extern IntPtr dlerror();\n\n"
        "        const int RTLD_NOW = 2;\n"
        "#elif LINUX\n        [DllImport(\"libdl.so.2\")]",
        "NativeLibrary.cs libdl.so P/Invoke ANDROID",
    )
    nl_text = replace_once(
        nl_text,
        "    public class NativeLibrary<T>",
        "#if ANDROID\n"
        "    public class DlErrorException : Exception\n"
        "    {\n"
        "        public DlErrorException() { }\n"
        "        public DlErrorException(string message) : base(message) { }\n"
        "        public DlErrorException(string message, Exception inner) : base(message, inner) { }\n"
        "    }\n"
        "#endif\n\n"
        "    public class NativeLibrary<T>",
        "NativeLibrary.cs DlErrorException class",
    )
    write_utf8(nl_path, nl_text)

    # 8. InternalUtils/UnityInformationHandler.cs
    ui_path = ml / "InternalUtils/UnityInformationHandler.cs"
    ui_text = ui_path.read_text(encoding="utf-8")
    ui_text = replace_once(
        ui_text,
        "            if (string.IsNullOrEmpty(GameDeveloper)\n"
        "                || string.IsNullOrEmpty(GameName))\n"
        "                ReadGameInfoFallback();",
        "#if !ANDROID\n"
        "            if (string.IsNullOrEmpty(GameDeveloper)\n"
        "                || string.IsNullOrEmpty(GameName))\n"
        "                ReadGameInfoFallback();\n"
        "#endif",
        "UnityInformationHandler.cs skip ReadGameInfoFallback on Android",
    )
    ui_text = replace_once(
        ui_text,
        "                string bundlePath = Path.Combine(gameDataPath, \"globalgamemanagers\");\n"
        "                if (!File.Exists(bundlePath))\n"
        "                    bundlePath = Path.Combine(gameDataPath, \"mainData\");\n\n"
        "                if (!File.Exists(bundlePath))\n"
        "                {\n"
        "                    bundlePath = Path.Combine(gameDataPath, \"data.unity3d\");\n"
        "                    if (!File.Exists(bundlePath))\n"
        "                        return;\n\n"
        "                    BundleFileInstance bundleFile = assetsManager.LoadBundleFile(bundlePath);\n"
        "                    instance = assetsManager.LoadAssetsFileFromBundle(bundleFile, \"globalgamemanagers\");\n"
        "                }\n"
        "                else\n"
        "                    instance = assetsManager.LoadAssetsFile(bundlePath, true);",
        "                string bundlePath = Path.Combine(gameDataPath, \"globalgamemanagers\");\n"
        "                if (!FileExists(bundlePath))\n"
        "                    bundlePath = Path.Combine(gameDataPath, \"mainData\");\n\n"
        "                if (!FileExists(bundlePath))\n"
        "                {\n"
        "                    bundlePath = Path.Combine(gameDataPath, \"data.unity3d\");\n"
        "                    if (!FileExists(bundlePath))\n"
        "                        return;\n\n"
        "#if !ANDROID\n"
        "                    BundleFileInstance bundleFile = assetsManager.LoadBundleFile(bundlePath);\n"
        "#else\n"
        "                    Stream bundleStream = APKAssetManager.GetAssetStream(bundlePath);\n"
        "                    BundleFileInstance bundleFile = assetsManager.LoadBundleFile(bundleStream, bundlePath);\n"
        "#endif\n"
        "                    instance = assetsManager.LoadAssetsFileFromBundle(bundleFile, \"globalgamemanagers\");\n"
        "                }\n"
        "                else\n"
        "                {\n"
        "#if !ANDROID\n"
        "                    instance = assetsManager.LoadAssetsFile(bundlePath, true);\n"
        "#else\n"
        "                    Stream bundleStream = APKAssetManager.GetAssetStream(bundlePath);\n"
        "                    instance = assetsManager.LoadAssetsFile(bundleStream, bundlePath, true);\n"
        "#endif\n"
        "                }",
        "UnityInformationHandler.cs APKAssetManager bundle/assets loading",
    )
    ui_text = ui_text.replace("File.Exists(", "FileExists(")
    ui_text = ui_text.replace("File.ReadAllBytes(", "FileReadAllBytes(")
    ui_text = ui_text.replace("File.OpenRead(", "FileOpenRead(")
    ui_text = replace_once(
        ui_text,
        "    }\n}",
        "        private static bool FileExists(string path) =>\n"
        "#if !ANDROID\n            File.Exists(path);\n#else\n            APKAssetManager.DoesAssetExist(path);\n#endif\n\n"
        "        private static byte[] FileReadAllBytes(string path) =>\n"
        "#if !ANDROID\n            File.ReadAllBytes(path);\n#else\n            APKAssetManager.GetAssetBytes(path);\n#endif\n\n"
        "        private static Stream FileOpenRead(string path) =>\n"
        "#if !ANDROID\n            File.OpenRead(path);\n#else\n            APKAssetManager.GetAssetStream(path);\n#endif\n"
        "    }\n}",
        "UnityInformationHandler.cs FileExists/FileReadAllBytes/FileOpenRead helpers",
    )
    write_utf8(ui_path, ui_text)

    print("[patched] MelonLoader managed core (LoaderConfig, BootstrapLibrary, Core, MelonUtils, MelonEnvironment, NativeLibrary, UnityInformationHandler)")


def patch_il2cpp_assembly_generator(root: Path) -> None:
    ag_dir = root / "Dependencies/Il2CppAssemblyGenerator"

    # Update Il2CppAssemblyGenerator.csproj to reference in-process Cpp2IL 2022.1.0-pre-release.21 when AndroidBuild=true
    ag_csproj = ag_dir / "Il2CppAssemblyGenerator.csproj"
    ag_csproj_text = ag_csproj.read_text(encoding="utf-8")
    ag_csproj_text = replace_once(
        ag_csproj_text,
        "        <PackageReference Include=\"Mono.Cecil\" Version=\"$(MonoCecilVersion)\" ExcludeAssets=\"Runtime\" />\n    </ItemGroup>",
        "        <PackageReference Include=\"Mono.Cecil\" Version=\"$(MonoCecilVersion)\" ExcludeAssets=\"Runtime\" />\n    </ItemGroup>\n\n"
        "    <ItemGroup Condition=\"'$(AndroidBuild)' == 'true'\">\n"
        "        <ProjectReference Include=\"$(SolutionDir)\\External\\Cpp2IL\\Cpp2IL.Core\\Cpp2IL.Core.csproj\" />\n"
        "        <ProjectReference Include=\"$(SolutionDir)\\External\\Cpp2IL\\LibCpp2IL\\LibCpp2IL.csproj\" />\n"
        "    </ItemGroup>",
        "Il2CppAssemblyGenerator.csproj Android Cpp2IL ProjectReferences",
    )
    write_utf8(ag_csproj, ag_csproj_text)

    # Patch Core.cs to resolve libil2cpp.so via JNI on Android and optionally download pregenerated interop
    ag_core = ag_dir / "Core.cs"
    ag_core_text = ag_core.read_text(encoding="utf-8")
    ag_core_text = replace_once(
        ag_core_text,
        "using System.IO;\nusing System.Net.Http;",
        "using System;\nusing System.IO;\nusing System.IO.Compression;\nusing System.Linq;\nusing System.Net;\nusing System.Net.Http;\n#if ANDROID\nusing MelonLoader.Java;\n#endif",
        "Il2CppAssemblyGenerator Core.cs usings",
    )
    ag_core_text = replace_once(
        ag_core_text,
        "            Cpp2IL cpp2IL_netcore = new Cpp2IL();\n"
        "            if (MelonUtils.IsWindows\n"
        "                && (cpp2IL_netcore.VersionSem < Cpp2IL.NetCoreMinVersion))",
        "            Packages.Cpp2IL cpp2IL_netcore = new Packages.Cpp2IL();\n"
        "            if (MelonUtils.IsWindows\n"
        "                && (cpp2IL_netcore.VersionSem < Packages.Cpp2IL.NetCoreMinVersion))",
        "Il2CppAssemblyGenerator Core.cs qualify Packages.Cpp2IL",
    )
    ag_core_text = replace_once(
        ag_core_text,
        "#if OSX\n            GameAssemblyPath = Path.Combine(MelonEnvironment.GameExecutablePath, \"Contents\", \"Frameworks\", gameAssemblyName);\n#else\n            GameAssemblyPath = Path.Combine(MelonEnvironment.GameRootDirectory, gameAssemblyName);\n#endif",
        "#if OSX\n            GameAssemblyPath = Path.Combine(MelonEnvironment.GameExecutablePath, \"Contents\", \"Frameworks\", gameAssemblyName);\n"
        "#elif ANDROID\n            GameAssemblyPath = GetLibIl2CppPath();\n"
        "#else\n            GameAssemblyPath = Path.Combine(MelonEnvironment.GameRootDirectory, gameAssemblyName);\n#endif",
        "Il2CppAssemblyGenerator Core.cs GetLibIl2CppPath",
    )
    ag_core_text = replace_once(
        ag_core_text,
        "            if (!AssemblyGenerationNeeded)\n            {\n                Logger.Msg(\"Assembly is up to date. No Generation Needed.\");\n                return 0;\n            }\n            Logger.Msg(\"Assembly Generation Needed!\");",
        "            if (!AssemblyGenerationNeeded)\n            {\n                Logger.Msg(\"Assembly is up to date. No Generation Needed.\");\n                return 0;\n            }\n            Logger.Msg(\"Assembly Generation Needed!\");\n\n"
        "#if ANDROID\n"
        "            if (!LoaderConfig.Current.UnityEngine.ForceOfflineGeneration)\n"
        "            {\n"
        "                Logger.Msg(\"Checking for Pre-Generated Assemblies...\");\n"
        "                if (TryDownloadPreGenerated(CurrentGameAssemblyHash))\n"
        "                {\n"
        "                    Logger.Msg($\"Extracting to {MelonEnvironment.MelonBaseDirectory}\");\n"
        "                    string zip = Path.Combine(BasePath, \"Pregenerated.zip\");\n"
        "                    ZipFile.ExtractToDirectory(zip, MelonEnvironment.MelonBaseDirectory, true);\n"
        "                    File.Delete(zip);\n"
        "                    Config.Values.GameAssemblyHash = CurrentGameAssemblyHash;\n"
        "                    Config.Save();\n"
        "                    return 0;\n"
        "                }\n"
        "            }\n"
        "#endif",
        "Il2CppAssemblyGenerator Core.cs pregenerated fast path",
    )
    ag_core_text = replace_once(
        ag_core_text,
        "        private static void OldFiles_Cleanup()",
        "#if ANDROID\n"
        "        private static bool TryDownloadPreGenerated(string hash)\n"
        "        {\n"
        "            const string PREGENERATED_TEMPLATE = \"https://github.com/LemonLoader/PregeneratedInteropData/raw/refs/heads/main/{0}.zip\";\n"
        "            try\n"
        "            {\n"
        "                webClient.DownloadFile(string.Format(PREGENERATED_TEMPLATE, hash), Path.Combine(BasePath, \"Pregenerated.zip\"));\n"
        "                return true;\n"
        "            }\n"
        "            catch (HttpRequestException ex) when (ex.StatusCode == HttpStatusCode.NotFound)\n"
        "            {\n"
        "                Logger.Msg(\"Pre-Generated Assemblies Not Found\");\n"
        "                return false;\n"
        "            }\n"
        "            catch (Exception ex)\n"
        "            {\n"
        "                Logger.Error($\"Exception while Downloading Pre-Generated Assemblies: {ex}\");\n"
        "                return false;\n"
        "            }\n"
        "        }\n\n"
        "        private static string GetLibIl2CppPath()\n"
        "        {\n"
        "            try\n"
        "            {\n"
        "                JClass unityClass = JNI.FindClass(\"com/unity3d/player/UnityPlayer\");\n"
        "                JFieldID activityFieldId = JNI.GetStaticFieldID(unityClass, \"currentActivity\", \"Landroid/app/Activity;\");\n"
        "                JObject currentActivityObj = JNI.GetStaticObjectField<JObject>(unityClass, activityFieldId);\n"
        "                JObject applicationInfoObj = JNI.CallObjectMethod<JObject>(currentActivityObj, JNI.GetMethodID(JNI.GetObjectClass(currentActivityObj), \"getApplicationInfo\", \"()Landroid/content/pm/ApplicationInfo;\"));\n"
        "                JFieldID filesFieldId = JNI.GetFieldID(JNI.GetObjectClass(applicationInfoObj), \"nativeLibraryDir\", \"Ljava/lang/String;\");\n"
        "                JString pathJString = JNI.GetObjectField<JString>(applicationInfoObj, filesFieldId);\n"
        "                if (pathJString == null || !pathJString.Valid())\n"
        "                    return \"\";\n"
        "                string nativePath = pathJString.GetString();\n"
        "                string[] directoryLibs = Directory.GetFiles(nativePath, \"*.so\");\n"
        "                return directoryLibs.FirstOrDefault(s => s.EndsWith(\"libil2cpp.so\")) ?? \"\";\n"
        "            }\n"
        "            catch (Exception ex)\n"
        "            {\n"
        "                Logger.Error($\"Failed resolving libil2cpp.so path: {ex}\");\n"
        "                return \"\";\n"
        "            }\n"
        "        }\n"
        "#endif\n\n"
        "        private static void OldFiles_Cleanup()",
        "Il2CppAssemblyGenerator Core.cs Android helpers",
    )
    write_utf8(ag_core, ag_core_text)

    # Patch Packages/Cpp2IL.cs to run in-process on Android using Cpp2IL.Core + APKAssetManager
    cpp2il_cs_path = ag_dir / "Packages/Cpp2IL.cs"
    orig_cpp2il = cpp2il_cs_path.read_text(encoding="utf-8")
    android_cpp2il = """#if !ANDROID
""" + orig_cpp2il + """
#else
using System;
using System.IO;
using System.Linq;
using AssetRipper.Primitives;
using Cpp2IL.Core;
using Cpp2IL.Core.Api;
using Cpp2IL.Core.Extensions;
using LibCpp2IL.Wasm;
using MelonLoader.InternalUtils;
using MelonLoader.Utils;
using Semver;

namespace MelonLoader.Il2CppAssemblyGenerator.Packages
{
    internal class Cpp2IL : Models.ExecutablePackage
    {
        internal static SemVersion NetCoreMinVersion = SemVersion.Parse("2022.1.0-pre-release.18");
        internal SemVersion VersionSem;

        internal Cpp2IL()
        {
            Version = "2022.1.0-pre-release.21";
            VersionSem = SemVersion.Parse(Version);
            Name = nameof(Cpp2IL);
            Destination = Path.Combine(Core.BasePath, Name);
            OutputFolder = Path.Combine(Destination, "cpp2il_out");
        }

        internal override bool ShouldSetup()
            => string.IsNullOrEmpty(Config.Values.DumperVersion)
            || !Config.Values.DumperVersion.Equals(Version);

        internal override void Cleanup() { }

        internal override void Save()
            => Save(ref Config.Values.DumperVersion);

        internal override bool Execute()
        {
            global::Cpp2IL.Core.Logging.Logger.InfoLog += (l, s) => Core.Logger.Msg($"[{s}] {l.TrimEnd('\\n')}");
            global::Cpp2IL.Core.Logging.Logger.WarningLog += (l, s) => Core.Logger.Warning($"[{s}] {l.TrimEnd('\\n')}");
            global::Cpp2IL.Core.Logging.Logger.ErrorLog += (l, s) => Core.Logger.Error($"[{s}] {l.TrimEnd('\\n')}");
            global::Cpp2IL.Core.Logging.Logger.VerboseLog += (l, s) => { if (MelonDebug.IsEnabled()) Core.Logger.Msg($"[{s}] {l.TrimEnd('\\n')}"); };

            byte[] mdData = APKAssetManager.GetAssetBytes("bin/Data/Managed/Metadata/global-metadata.dat");
            string mdPath = Path.Combine(Core.BasePath, "global-metadata.dat");
            Directory.CreateDirectory(Core.BasePath);
            File.WriteAllBytes(mdPath, mdData);

            Cpp2IlApi.Init();
            Cpp2IlApi.ConfigureLib(false);
            var result = new Cpp2IlRuntimeArgs
            {
                PathToAssembly = Core.GameAssemblyPath,
                PathToMetadata = mdPath,
                UnityVersion = UnityVersion.Parse(UnityInformationHandler.EngineVersion.ToString()),
                Valid = true,
                OutputRootDirectory = OutputFolder,
                OutputFormat = OutputFormatRegistry.GetFormat("dummydll"),
                ProcessingLayersToRun = new[]
                {
                    ProcessingLayerRegistry.GetById("attributeanalyzer"),
                    ProcessingLayerRegistry.GetById("attributeinjector"),
                }.ToList(),
            };

            return RunCpp2IL(result);
        }

        private static bool RunCpp2IL(Cpp2IlRuntimeArgs runtimeArgs)
        {
            var executionStart = DateTime.Now;
            Cpp2IlApi.RuntimeOptions = runtimeArgs;
            runtimeArgs.OutputFormat?.OnOutputFormatSelected();
            WasmFile.RemappedDynCallFunctions = null;

            Cpp2IlApi.InitializeLibCpp2Il(runtimeArgs.PathToAssembly, runtimeArgs.PathToMetadata, runtimeArgs.UnityVersion);

            foreach (var (key, value) in runtimeArgs.ProcessingLayerConfigurationOptions)
                Cpp2IlApi.CurrentAppContext.PutExtraData(key, value);

            var layers = runtimeArgs.ProcessingLayersToRun.Clone();
            foreach (var l in layers) l.PreProcess(Cpp2IlApi.CurrentAppContext, layers);
            runtimeArgs.ProcessingLayersToRun = layers;
            foreach (var l in runtimeArgs.ProcessingLayersToRun) l.Process(Cpp2IlApi.CurrentAppContext);

            if (runtimeArgs.OutputFormat != null)
            {
                Directory.CreateDirectory(runtimeArgs.OutputRootDirectory);
                runtimeArgs.OutputFormat.DoOutput(Cpp2IlApi.CurrentAppContext, runtimeArgs.OutputRootDirectory);
            }

            Cpp2IlPluginManager.CallOnFinish();
            try { if (File.Exists(runtimeArgs.PathToMetadata)) File.Delete(runtimeArgs.PathToMetadata); } catch { }

            Core.Logger.Msg($"Cpp2IL Done in {(DateTime.Now - executionStart).TotalMilliseconds:F0}ms");
            return true;
        }
    }
}
#endif
"""
    write_utf8(cpp2il_cs_path, android_cpp2il)

    # Isolate External/Cpp2IL from MelonLoader's root Directory.Build.props when present
    ext_cpp2il_dir = root / "External/Cpp2IL"
    if ext_cpp2il_dir.exists():
        write_utf8(
            ext_cpp2il_dir / "Directory.Build.props",
            "<Project>\n    <PropertyGroup>\n        <LangVersion>Latest</LangVersion>\n    </PropertyGroup>\n</Project>\n",
        )

    # Skip Cpp2IL_StrippedCodeRegSupport download on Android (in-process Cpp2IL doesn't load plugins from CWD)
    scrs_path = root / "Dependencies/Il2CppAssemblyGenerator/Packages/Cpp2IL_StrippedCodeRegSupport.cs"
    if scrs_path.exists():
        scrs_text = scrs_path.read_text(encoding="utf-8")
        if "#if ANDROID" not in scrs_text:
            scrs_text = replace_once(
                scrs_text,
                "        internal override bool Setup()\n        {",
                "        internal override bool Setup()\n        {\n#if ANDROID\n            return true;\n#endif",
                "Cpp2IL_StrippedCodeRegSupport.cs skip on Android",
            )
            write_utf8(scrs_path, scrs_text)

    # Align Cpp2IL LibCpp2IL AssetRipper.Primitives to 3.2.0 if present
    libcpp2il_csproj = root / "External/Cpp2IL/LibCpp2IL/LibCpp2IL.csproj"
    if libcpp2il_csproj.exists():
        txt = libcpp2il_csproj.read_text(encoding="utf-8")
        txt = re.sub(
            r'<PackageReference Include="AssetRipper\.Primitives" Version="[^"]+"',
            '<PackageReference Include="AssetRipper.Primitives" Version="3.2.0"',
            txt,
        )
        write_utf8(libcpp2il_csproj, txt)

    print("[patched] Il2CppAssemblyGenerator (Android in-process Cpp2IL + JNI libil2cpp.so lookup)")


def main() -> int:
    if len(sys.argv) != 3:
        print(
            f"Usage: {sys.argv[0]} <path-to-LavaGang-MelonLoader-0.7.3> <path-to-LemonLoader-0.7.0>",
            file=sys.stderr,
        )
        return 2

    root = Path(sys.argv[1]).resolve()
    lemon_070 = Path(sys.argv[2]).resolve()

    if not (root / "MelonLoader/MelonLoader.csproj").exists():
        print(f"Error: {root} does not look like LavaGang/MelonLoader.", file=sys.stderr)
        return 1
    if not (lemon_070 / "MelonLoader/JNI/JNI.cs").exists():
        print(f"Error: {lemon_070} does not look like LemonLoader 0.7.0.", file=sys.stderr)
        return 1

    copy_lemon070_android_files(root, lemon_070)
    write_early_crash_c_helper(root)
    patch_directory_build_props(root)
    patch_bootstrap_targets_and_csproj(root)
    write_android_proxy_and_early_log(root)
    patch_bootstrap_runtime_files(root)
    patch_managed_melonloader(root)
    patch_il2cpp_assembly_generator(root)
    print("[success] LavaGang/MelonLoader v0.7.3 Open-Beta Android ARM64 port applied cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
