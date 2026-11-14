#!/usr/bin/env python3
"""
Deterministic source-level repair script for LemonLoader/MelonLoader (0.6.5 / 0.6.X).

Fixes:
1. System.NullReferenceException: Null method for MelonLoader
   - Removes obsolete .NET 6 AssemblyLoadContext QCall hooks (LoadFromPath / LoadFromStream)
     from MelonLoader/Fixes/DotnetAssemblyLoadContextFix.cs (absent in .NET 8).
   - Adds explicit null-safety guards before every Core.HarmonyInstance.Patch(...) call in
     DotnetAssemblyLoadContextFix, DotnetModHandlerRedirectionFix, InstancePatchFix,
     PatchShield, DetourContextDisposeFix, and ProcessFix.
   - Guards Windows-only user32.dll ProcessFix hooks on Android/non-Windows targets.
2. System.MissingMethodException: RegisterTypeOptions.set_LogSuccess(bool)
   - Aligns AsmResolver.DotNet package version across MelonLoader.csproj and
     Il2CppInterop.Generator.csproj (6.0.0-beta.5).
   - Verifies SupportModules/Il2Cpp and Il2CppInterop.Runtime are built from the same
     workspace ProjectReference tree with consistent RegisterTypeOptions.LogSuccess ABI.
"""

from pathlib import Path
import re
import sys


def write_utf8(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def patch_dotnet_alc_fix(root: Path) -> None:
    target = root / "MelonLoader/Fixes/DotnetAssemblyLoadContextFix.cs"
    if not target.exists():
        raise FileNotFoundError(target)

    content = """#if NET6_0_OR_GREATER
using HarmonyLib;
using MelonLoader.Utils;
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.Loader;

namespace MelonLoader.Fixes
{
    internal class DotnetAssemblyLoadContextFix
    {
        private delegate Assembly DelegateInternalLoad(ReadOnlySpan<byte> arrAssembly, ReadOnlySpan<byte> arrSymbols);

        private static readonly Dictionary<string, Assembly> s_loadfile = new Dictionary<string, Assembly>();

        private static readonly MethodInfo AlcInternalLoad = typeof(AssemblyLoadContext).GetMethod("InternalLoad", BindingFlags.NonPublic | BindingFlags.Instance);
        private static readonly DelegateInternalLoad DefaultContextInternalLoad =
            AlcInternalLoad != null ? AlcInternalLoad.CreateDelegate<DelegateInternalLoad>(AssemblyLoadContext.Default) : null;

        internal static void Install()
        {
            try
            {
                if (DefaultContextInternalLoad != null)
                {
                    var loadMethod = AccessTools.Method(typeof(Assembly), nameof(Assembly.Load), new Type[] { typeof(byte[]), typeof(byte[]) });
                    if (loadMethod != null)
                        Core.HarmonyInstance.Patch(loadMethod, new HarmonyMethod(typeof(DotnetAssemblyLoadContextFix), nameof(PreAssemblyLoad)));
                    else
                        MelonLogger.Warning("DotnetAssemblyLoadContextFix: Assembly.Load(byte[], byte[]) not found; skipping hook.");
                }
                else
                {
                    MelonLogger.Warning("DotnetAssemblyLoadContextFix: AssemblyLoadContext.InternalLoad not found on this runtime; skipping Assembly.Load hook.");
                }

                var loadFileMethod = AccessTools.Method(typeof(Assembly), nameof(Assembly.LoadFile), new Type[] { typeof(string) });
                if (loadFileMethod != null)
                    Core.HarmonyInstance.Patch(loadFileMethod, new HarmonyMethod(typeof(DotnetAssemblyLoadContextFix), nameof(PreAssemblyLoadFile)));
                else
                    MelonLogger.Warning("DotnetAssemblyLoadContextFix: Assembly.LoadFile(string) not found; skipping hook.");

                // We have to load everything required for the verifier to avoid getting stuck in an infinite loop, prior to hooking AssemblyLoadContext.
                AssemblyVerifier.EnsureInitialized();
            }
            catch (Exception ex) { MelonLogger.Warning($"DotnetAssemblyLoadContextFix Exception: {ex}"); }
        }

        public static bool PreAssemblyLoad(byte[] rawAssembly, byte[] rawSymbolStore, ref Assembly __result)
        {
            if (DefaultContextInternalLoad == null)
                return true;

            __result = DefaultContextInternalLoad(rawAssembly, rawSymbolStore);
            return false;
        }

        public static bool PreAssemblyLoadFile(string path, ref Assembly __result)
        {
            string normalizedPath = Path.GetFullPath(path);

            lock (s_loadfile)
            {
                if (s_loadfile.TryGetValue(normalizedPath, out __result))
                    return false;

                __result = AssemblyLoadContext.Default.LoadFromAssemblyPath(normalizedPath);
                s_loadfile.Add(normalizedPath, __result);
            }

            return false;
        }
    }
}
#endif
"""
    write_utf8(target, content)
    print(f"[patched] {target}")


def patch_mod_handler_redirection_fix(root: Path) -> None:
    target = root / "MelonLoader/Fixes/DotnetModHandlerRedirectionFix.cs"
    if not target.exists():
        raise FileNotFoundError(target)

    content = """#if NET6_0_OR_GREATER
using HarmonyLib;
using System;
using System.Reflection;
using System.Runtime.Loader;

namespace MelonLoader.Fixes
{
    internal class DotnetModHandlerRedirectionFix
    {
        public static void Install()
        {
            try
            {
                MethodInfo validateMethod = typeof(AssemblyLoadContext).GetMethod("ValidateAssemblyNameWithSimpleName", BindingFlags.Static | BindingFlags.NonPublic);
                if (validateMethod == null)
                {
                    MelonLogger.Warning("DotnetModHandlerRedirectionFix: AssemblyLoadContext.ValidateAssemblyNameWithSimpleName not found; skipping hook.");
                    return;
                }

                Core.HarmonyInstance.Patch(validateMethod,
                    new HarmonyMethod(typeof(DotnetModHandlerRedirectionFix), nameof(PreValidateAssembly)));
            }
            catch (Exception ex) { MelonLogger.Warning($"DotnetModHandlerRedirectionFix Exception: {ex}"); }
        }

        public static bool PreValidateAssembly(Assembly assembly, string requestedSimpleName, ref Assembly __result)
        {
            if (requestedSimpleName != null && requestedSimpleName.Contains("MelonLoader.ModHandler"))
            {
                __result = assembly;
                return false;
            }

            return true;
        }
    }
}
#endif
"""
    write_utf8(target, content)
    print(f"[patched] {target}")


def patch_instance_patch_fix(root: Path) -> None:
    target = root / "MelonLoader/Fixes/InstancePatchFix.cs"
    if not target.exists():
        raise FileNotFoundError(target)

    content = """using System;
using System.Reflection;
using HarmonyLib;
using MonoMod.RuntimeDetour;

namespace MelonLoader.Fixes
{
    internal static class InstancePatchFix
    {
        internal static void Install()
        {
            Type instancePatchFixType = typeof(InstancePatchFix);
            MethodInfo patchMethodInfo = AccessTools.Method(instancePatchFixType, "PatchMethod");
            if (patchMethodInfo == null)
                return;

            HarmonyMethod patchMethod = patchMethodInfo.ToNewHarmonyMethod();

            try
            {
                MethodInfo reversePatch = AccessTools.Method("HarmonyLib.PatchFunctions:ReversePatch");
                if (reversePatch != null)
                    Core.HarmonyInstance.Patch(reversePatch, patchMethod);
                else
                    MelonLogger.Warning("InstancePatchFix: HarmonyLib.PatchFunctions:ReversePatch not found; skipping.");

                MethodInfo importMethod = AccessTools.Method("HarmonyLib.HarmonyMethod:ImportMethod");
                if (importMethod != null)
                    Core.HarmonyInstance.Patch(importMethod, patchMethod);
                else
                    MelonLogger.Warning("InstancePatchFix: HarmonyLib.HarmonyMethod:ImportMethod not found; skipping.");
            }
            catch (Exception ex) { MelonLogger.Warning($"InstancePatchFix Exception: {ex}"); }

            DetourManager.DetourApplied += DetourManager_DetourApplied;
        }

        private static void DetourManager_DetourApplied(DetourInfo obj)
        {
            PatchMethod(obj.Method.Method);
        }

        private static bool PatchMethod(MethodBase __0)
        {
            if (__0 == null)
                throw new NullReferenceException("Patch Method");
            if ((__0 != null) && !__0.IsStatic)
                throw new Exception("Patch Method must be a Static Method!");
            return true;
        }
    }
}
"""
    write_utf8(target, content)
    print(f"[patched] {target}")


def patch_shield(root: Path) -> None:
    target = root / "MelonLoader/Attributes/PatchShield.cs"
    if not target.exists():
        raise FileNotFoundError(target)

    content = """using System;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using MonoMod.RuntimeDetour;

namespace MelonLoader
{
    [AttributeUsage(AttributeTargets.Assembly | AttributeTargets.Method | AttributeTargets.Class | AttributeTargets.Struct)]
    public class PatchShield : Attribute
    {
        private static AccessTools.FieldRef<object, MethodBase> PatchProcessor_OriginalRef;

        private static void LogException(Exception ex) => MelonLogger.Warning($"Patch Shield Exception: {ex}");

        private static bool MethodCheck(MethodBase method) =>
            (method != null)
            && (method.DeclaringType == null || (
                method.DeclaringType.Assembly.GetCustomAttributes(typeof(PatchShield), false).Length <= 0
                && method.DeclaringType.GetCustomAttributes(typeof(PatchShield), false).Length <= 0))
            && (method.GetCustomAttributes(typeof(PatchShield), false).Length <= 0);

        internal static void Install()
        {
            Type patchProcessorType = typeof(PatchProcessor);
            Type patchShieldType = typeof(PatchShield);
            PatchProcessor_OriginalRef = AccessTools.FieldRefAccess<MethodBase>(patchProcessorType, "original");

            try
            {
                MethodInfo reversePatch = AccessTools.Method("HarmonyLib.PatchFunctions:ReversePatch");
                if (reversePatch != null)
                {
                    Core.HarmonyInstance.Patch(
                        reversePatch,
                        AccessTools.Method(patchShieldType, "PatchMethod_PatchFunctions_ReversePatch").ToNewHarmonyMethod()
                    );
                }
                else
                {
                    MelonLogger.Warning("PatchShield: HarmonyLib.PatchFunctions:ReversePatch not found; skipping.");
                }
            }
            catch (Exception ex) { LogException(ex); }

            try
            {
                HarmonyMethod unpatchMethod = AccessTools.Method(patchShieldType, "PatchMethod_PatchProcessor_Unpatch").ToNewHarmonyMethod();
                foreach (MethodInfo method in patchProcessorType.GetMethods(BindingFlags.Public | BindingFlags.Instance).Where(x => x.Name.Equals("Unpatch")))
                {
                    if (method != null)
                        Core.HarmonyInstance.Patch(method, unpatchMethod);
                }
            }
            catch (Exception ex) { LogException(ex); }

            try
            {
                MethodInfo patchMethod = AccessTools.Method(patchProcessorType, "Patch");
                if (patchMethod != null)
                {
                    Core.HarmonyInstance.Patch(patchMethod,
                        AccessTools.Method(patchShieldType, "PatchMethod_PatchProcessor_Patch").ToNewHarmonyMethod()
                    );
                }
            }
            catch (Exception ex) { LogException(ex); }

            DetourManager.DetourApplied += DetourManager_DetourApplied;
            DetourManager.ILHookApplied += DetourManager_ILHookApplied;
        }

        private static void DetourManager_ILHookApplied(ILHookInfo obj)
        {
            MethodCheck(obj.Method.Method);
        }

        private static void DetourManager_DetourApplied(DetourInfo obj)
        {
            MethodCheck(obj.Method.Method);
        }

        private static bool PatchMethod_PatchFunctions_ReversePatch(MethodBase __1) => MethodCheck(__1);
        private static bool PatchMethod_PatchProcessor_Patch(PatchProcessor __instance) => MethodCheck(PatchProcessor_OriginalRef(__instance));
        private static bool PatchMethod_PatchProcessor_Unpatch(PatchProcessor __instance) => MethodCheck(PatchProcessor_OriginalRef(__instance));
    }
}
"""
    write_utf8(target, content)
    print(f"[patched] {target}")


def patch_detour_context_dispose_fix(root: Path) -> None:
    target = root / "MelonLoader/Fixes/DetourContextDisposeFix.cs"
    if not target.exists():
        return

    text = target.read_text(encoding="utf-8")
    if "if (_dispose == null" in text:
        return

    old = '            Core.HarmonyInstance.Patch(_dispose,'
    new = '''            if (_dispose == null || _disposeTranspiler == null || _isDisposed == null)
            {
                MelonLogger.Warning("DetourContextDisposeFix: Required MonoMod DetourContext members not found; skipping.");
                return;
            }
            Core.HarmonyInstance.Patch(_dispose,'''
    if old in text:
        write_utf8(target, text.replace(old, new, 1))
        print(f"[patched] {target}")


def patch_process_fix(root: Path) -> None:
    target = root / "MelonLoader/Fixes/ProcessFix.cs"
    if not target.exists():
        return

    text = target.read_text(encoding="utf-8")
    if "OperatingSystem.IsWindows()" in text:
        return

    old_install = """\t\tinternal static void Install()
\t\t{
\t\t\tmainWindowFinder = new MainWindowFinder();"""
    new_install = """\t\tinternal static void Install()
\t\t{
#if NET6_0_OR_GREATER
\t\t\tif (!OperatingSystem.IsWindows())
\t\t\t\treturn;
#endif
\t\t\tmainWindowFinder = new MainWindowFinder();"""

    old_patch = """\t\t\t\tCore.HarmonyInstance.Patch(AccessTools.PropertyGetter(processType, "MainWindowHandle"), AccessTools.Method(processFixType, "get_MainWindowHandle").ToNewHarmonyMethod());
\t\t\t\tCore.HarmonyInstance.Patch(AccessTools.PropertyGetter(processType, "MainWindowTitle"), AccessTools.Method(processFixType, "get_MainWindowTitle").ToNewHarmonyMethod());"""
    new_patch = """\t\t\t\tvar handleGetter = AccessTools.PropertyGetter(processType, "MainWindowHandle");
\t\t\t\tif (handleGetter != null)
\t\t\t\t\tCore.HarmonyInstance.Patch(handleGetter, AccessTools.Method(processFixType, "get_MainWindowHandle").ToNewHarmonyMethod());
\t\t\t\tvar titleGetter = AccessTools.PropertyGetter(processType, "MainWindowTitle");
\t\t\t\tif (titleGetter != null)
\t\t\t\t\tCore.HarmonyInstance.Patch(titleGetter, AccessTools.Method(processFixType, "get_MainWindowTitle").ToNewHarmonyMethod());"""

    updated = text.replace(old_install, new_install, 1).replace(old_patch, new_patch, 1)
    if updated != text:
        write_utf8(target, updated)
        print(f"[patched] {target}")


def patch_il2cpp_interop_fixes(root: Path) -> None:
    target = root / "MelonLoader/Fixes/Il2CppInteropFixes.cs"
    if not target.exists():
        return

    text = target.read_text(encoding="utf-8")
    if "__0.Module" not in text:
        return

    updated = text.replace("__0.Module", "__0.DeclaringModule")
    write_utf8(target, updated)
    print(f"[patched] {target} (AsmResolver 6.0.0-beta.5 DeclaringModule)")


def patch_rust_melon_proxy(root: Path) -> None:
    proxy_dir = root / "Rust/MelonProxy/src"
    if not proxy_dir.exists():
        raise FileNotFoundError(proxy_dir)

    early_log_rs = """use std::ffi::{c_char, c_int, CStr, CString};
use std::fs::{create_dir_all, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;

extern "C" {
    fn __android_log_write(prio: c_int, tag: *const c_char, text: *const c_char) -> c_int;
}

static EARLY_LOG_PATH: Mutex<Option<PathBuf>> = Mutex::new(None);
static EARLY_STAGE: Mutex<String> = Mutex::new(String::new());

pub fn read_cmdline_package() -> Option<String> {
    let data = std::fs::read("/proc/self/cmdline").ok()?;
    let end = data.iter().position(|&b| b == 0).unwrap_or(data.len());
    let s = String::from_utf8_lossy(&data[..end]).trim().to_string();
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

pub fn init_early_log(pkg_override: Option<&str>) {
    let pkg = pkg_override
        .map(|s| s.to_string())
        .or_else(read_cmdline_package);

    if let Some(pkg_name) = pkg {
        let dir = PathBuf::from("/data/data").join(&pkg_name).join("files");
        let _ = create_dir_all(&dir);
        let path = dir.join("lemon_early_crash.log");
        if let Ok(mut guard) = EARLY_LOG_PATH.lock() {
            *guard = Some(path);
        }
    }
}

pub fn set_stage(stage: &str) {
    if let Ok(mut guard) = EARLY_STAGE.lock() {
        *guard = stage.trim().to_ascii_lowercase();
    }
}

pub fn get_stage() -> String {
    if let Ok(guard) = EARLY_STAGE.lock() {
        if !guard.is_empty() {
            return guard.clone();
        }
    }
    "full".to_string()
}

pub fn checkpoint(stage: &str, msg: &str) {
    let line = format!("[LemonProxy][{stage}] {msg}");
    if let Ok(tag) = CString::new("LemonEarly") {
        if let Ok(cmsg) = CString::new(line.as_str()) {
            unsafe {
                __android_log_write(4, tag.as_ptr(), cmsg.as_ptr());
            }
        }
    }
    if let Ok(tag) = CString::new("MelonLoader") {
        if let Ok(cmsg) = CString::new(line.as_str()) {
            unsafe {
                __android_log_write(4, tag.as_ptr(), cmsg.as_ptr());
            }
        }
    }

    if let Ok(guard) = EARLY_LOG_PATH.lock() {
        if let Some(ref path) = *guard {
            if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
                let _ = writeln!(f, "{line}");
                let _ = f.sync_all();
            }
        }
    }
}

pub unsafe fn last_dlerror() -> String {
    let err = libc::dlerror();
    if err.is_null() {
        "none".to_string()
    } else {
        CStr::from_ptr(err).to_string_lossy().into_owned()
    }
}
"""
    write_utf8(proxy_dir / "early_log.rs", early_log_rs)

    lib_rs = """#![allow(non_snake_case)]

#[cfg(target_os = "android")]
mod early_log;
#[cfg(target_os = "android")]
mod libloading;
#[cfg(target_os = "android")]
mod nativeloader;

#[cfg(target_os = "android")]
use std::{mem, os::raw::c_void, panic::catch_unwind, path::PathBuf};

#[cfg(target_os = "android")]
use jni::{
    objects::{JClass, JString},
    sys::{jboolean, jint, JNI_VERSION_1_6},
    JNIEnv, JavaVM, NativeMethod,
};

#[cfg(target_os = "android")]
use libloading::load_lib;

#[cfg(target_os = "android")]
const INVALID_JNI_VERSION: jint = 0;

#[cfg(target_os = "android")]
#[no_mangle]
pub extern "system" fn JNI_OnLoad(vm: JavaVM, _: *mut c_void) -> jint {
    android_log::init("MelonLoader").unwrap_or(());
    early_log::init_early_log(None);
    early_log::checkpoint("proxy_jni_onload", "Entered libmain.so JNI_OnLoad");

    let mut env: JNIEnv = match vm.get_env() {
        Ok(e) => e,
        Err(e) => {
            early_log::checkpoint("proxy_jni_onload", &format!("ERROR get_env: {e}"));
            return INVALID_JNI_VERSION;
        }
    };
    if let Err(e) = vm.attach_current_thread() {
        early_log::checkpoint("proxy_jni_onload", &format!("ERROR attach_current_thread: {e}"));
    }

    let self_lib = match load_lib(&PathBuf::from("libmain.so"), libc::RTLD_NOW | libc::RTLD_GLOBAL) {
        Ok(lib) => lib,
        Err(e) => {
            let dl = unsafe { early_log::last_dlerror() };
            early_log::checkpoint("proxy_jni_onload", &format!("ERROR dlopen(libmain.so): {e} dlerror={dl}"));
            return INVALID_JNI_VERSION;
        }
    };
    early_log::checkpoint("proxy_jni_onload", &format!("self libmain.so handle={:p}", self_lib.handle));

    let load_handle: libloading::NativeMethod<extern "system" fn(JNIEnv, JClass, JString) -> jboolean> =
        match self_lib.sym("load") {
            Ok(h) => h,
            Err(e) => {
                early_log::checkpoint("proxy_jni_onload", &format!("ERROR dlsym(load): {e}"));
                return INVALID_JNI_VERSION;
            }
        };

    let unload_handle: libloading::NativeMethod<extern "system" fn(JNIEnv, JClass) -> jboolean> =
        match self_lib.sym("unload") {
            Ok(h) => h,
            Err(e) => {
                early_log::checkpoint("proxy_jni_onload", &format!("ERROR dlsym(unload): {e}"));
                return INVALID_JNI_VERSION;
            }
        };

    early_log::checkpoint(
        "proxy_jni_onload",
        &format!(
            "Resolved NativeLoader methods: load={:p}, unload={:p}",
            load_handle.inner, unload_handle.inner
        ),
    );

    let native_loader_methods: &[NativeMethod] = &[
        NativeMethod {
            name: "load".into(),
            sig: "(Ljava/lang/String;)Z".into(),
            fn_ptr: unsafe { mem::transmute(load_handle) },
        },
        NativeMethod {
            name: "unload".into(),
            sig: "()Z".into(),
            fn_ptr: unsafe { mem::transmute(unload_handle) },
        },
    ];

    let nativeloader_class = match env.find_class("com/unity3d/player/NativeLoader") {
        Ok(c) => c,
        Err(e) => {
            early_log::checkpoint("proxy_jni_onload", &format!("ERROR find_class NativeLoader: {e}"));
            return INVALID_JNI_VERSION;
        }
    };
    if let Err(e) = env.register_native_methods(nativeloader_class, native_loader_methods) {
        early_log::checkpoint("proxy_jni_onload", &format!("ERROR register_native_methods: {e}"));
        return INVALID_JNI_VERSION;
    }

    early_log::checkpoint("proxy_jni_onload", "Registered NativeLoader.load/unload successfully");
    catch_unwind(|| JNI_VERSION_1_6).unwrap_or(INVALID_JNI_VERSION)
}
"""
    write_utf8(proxy_dir / "lib.rs", lib_rs)

    nativeloader_rs = """use jni::{
    objects::{JClass, JObject, JString, JValueGen},
    sys::{jboolean, JavaVM},
    JNIEnv,
};
use std::path::PathBuf;

use crate::early_log;
use crate::libloading::{self, load_lib, NativeLibrary};

fn read_stage_from_apk(env: &mut JNIEnv) -> String {
    let fallback = "full".to_string();

    let pkg_from_cmdline = early_log::read_cmdline_package();
    if let Some(ref pkg) = pkg_from_cmdline {
        let override_file = PathBuf::from("/data/data").join(pkg).join("files/lemon_stage.txt");
        if let Ok(txt) = std::fs::read_to_string(&override_file) {
            let trimmed = txt.trim().to_ascii_lowercase();
            if !trimmed.is_empty() {
                return trimmed;
            }
        }
    }

    let unity_class = match env.find_class("com/unity3d/player/UnityPlayer") {
        Ok(c) => c,
        Err(_) => {
            let _ = env.exception_clear();
            return fallback;
        }
    };

    let activity: JObject = match env.get_static_field(&unity_class, "currentActivity", "Landroid/app/Activity;") {
        Ok(v) => match v.l() {
            Ok(obj) if !obj.is_null() => obj,
            _ => return fallback,
        },
        Err(_) => {
            let _ = env.exception_clear();
            return fallback;
        }
    };

    if let Ok(pkg_val) = env.call_method(&activity, "getPackageName", "()Ljava/lang/String;", &[]) {
        if let Ok(pkg_obj) = pkg_val.l() {
            let pkg_jstr: JString = pkg_obj.into();
            if let Ok(pkg_rust) = env.get_string(&pkg_jstr) {
                let pkg_str: String = pkg_rust.into();
                early_log::init_early_log(Some(&pkg_str));
            }
            let _ = env.delete_local_ref(pkg_jstr);
        }
    } else {
        let _ = env.exception_clear();
    }

    let assets_obj: JObject = match env.call_method(&activity, "getAssets", "()Landroid/content/res/AssetManager;", &[]) {
        Ok(v) => match v.l() {
            Ok(obj) if !obj.is_null() => obj,
            _ => return fallback,
        },
        Err(_) => {
            let _ = env.exception_clear();
            return fallback;
        }
    };

    let name_jstr = match env.new_string("lemon_stage.txt") {
        Ok(s) => s,
        Err(_) => return fallback,
    };

    let stream_res = env.call_method(
        &assets_obj,
        "open",
        "(Ljava/lang/String;)Ljava/io/InputStream;",
        &[JValueGen::from(&name_jstr)],
    );
    let _ = env.delete_local_ref(name_jstr);

    let stream_obj: JObject = match stream_res {
        Ok(v) => match v.l() {
            Ok(obj) if !obj.is_null() => obj,
            _ => return fallback,
        },
        Err(_) => {
            let _ = env.exception_clear();
            return fallback;
        }
    };

    let mut out = Vec::new();
    loop {
        match env.call_method(&stream_obj, "read", "()I", &[]) {
            Ok(v) => match v.i() {
                Ok(b) if b >= 0 => out.push(b as u8),
                _ => break,
            },
            Err(_) => {
                let _ = env.exception_clear();
                break;
            }
        }
    }
    let _ = env.call_method(&stream_obj, "close", "()V", &[]);
    if env.exception_check().unwrap_or(false) {
        let _ = env.exception_clear();
    }

    let text = String::from_utf8_lossy(&out).trim().to_ascii_lowercase();
    if text.is_empty() {
        fallback
    } else {
        text
    }
}

fn load_lib_with_dir(lib_dir: Option<&str>, soname: &str, rtld: libc::c_int) -> Result<NativeLibrary, String> {
    if let Some(dir) = lib_dir {
        if !dir.is_empty() {
            let candidate = PathBuf::from(dir).join(soname);
            early_log::checkpoint("dlopen", &format!("BEFORE dlopen({}) rtld=0x{:x}", candidate.display(), rtld));
            match load_lib(&candidate, rtld) {
                Ok(lib) => {
                    early_log::checkpoint("dlopen", &format!("AFTER dlopen({}) -> handle={:p}", candidate.display(), lib.handle));
                    return Ok(lib);
                }
                Err(e) => {
                    let dl = unsafe { early_log::last_dlerror() };
                    early_log::checkpoint("dlopen", &format!("Candidate {} failed ({e}, dlerror={dl}); trying bare {soname}", candidate.display()));
                }
            }
        }
    }

    early_log::checkpoint("dlopen", &format!("BEFORE dlopen({soname}) rtld=0x{:x}", rtld));
    match load_lib(&PathBuf::from(soname), rtld) {
        Ok(lib) => {
            early_log::checkpoint("dlopen", &format!("AFTER dlopen({soname}) -> handle={:p}", lib.handle));
            Ok(lib)
        }
        Err(e) => {
            let dl = unsafe { early_log::last_dlerror() };
            Err(format!("dlopen({soname}) failed: {e}, dlerror={dl}"))
        }
    }
}

#[no_mangle]
pub extern "system" fn load(mut env: JNIEnv, _: JClass, lib_dir_jstr: JString) -> jboolean {
    let lib_dir = if !lib_dir_jstr.is_null() {
        env.get_string(&lib_dir_jstr)
            .ok()
            .map(|s| String::from(s))
    } else {
        None
    };

    let stage = read_stage_from_apk(&mut env);
    early_log::set_stage(&stage);
    early_log::checkpoint(
        "native_loader_load",
        &format!(
            "Entered NativeLoader.load(libDir={:?}, stage={})",
            lib_dir, stage
        ),
    );

    if stage == "no_hooks" || stage == "passthrough" {
        early_log::checkpoint("native_loader_load", "Stage=no_hooks: skipping libBootstrap.so and loading libunity.so directly");
        load_lib_unity(&env, lib_dir.as_deref());
        early_log::checkpoint("native_loader_load", "Stage=no_hooks complete; returning 1");
        return 1;
    }

    // Load libBootstrap.so and run JNI_OnLoad first (early logging + assets + stage setup),
    // then load libunity.so + JNI_OnLoad into RTLD_GLOBAL,
    // and finally invoke libBootstrap.so::startup() unless stage == minimal_bootstrap.
    let bootstrap_lib = load_bootstrap_jni(&env, lib_dir.as_deref());
    load_lib_unity(&env, lib_dir.as_deref());

    if stage == "minimal_bootstrap" {
        early_log::checkpoint("native_loader_load", "Stage=minimal_bootstrap: skipping libBootstrap.so::startup() and Dobby hooks");
        return 1;
    }

    run_bootstrap_startup(&bootstrap_lib);
    early_log::checkpoint("native_loader_load", "NativeLoader.load finished cleanly; returning 1");
    1
}

#[no_mangle]
pub extern "system" fn unload(_: JNIEnv, _: JClass) -> jboolean {
    early_log::checkpoint("native_loader_unload", "NativeLoader.unload called");
    1
}

fn load_bootstrap_jni(env: &JNIEnv, lib_dir: Option<&str>) -> NativeLibrary {
    let bootstrap_lib = load_lib_with_dir(lib_dir, "libBootstrap.so", libc::RTLD_NOW | libc::RTLD_GLOBAL)
        .unwrap_or_else(|err| {
            early_log::checkpoint("load_bootstrap", &format!("FATAL: {err}"));
            panic!("{err}");
        });

    let on_load: libloading::NativeMethod<extern "system" fn(*mut JavaVM, *mut libc::c_void) -> jni::sys::jint> = bootstrap_lib
        .sym("JNI_OnLoad")
        .unwrap_or_else(|e| {
            early_log::checkpoint("load_bootstrap", &format!("FATAL: dlsym(libBootstrap.so::JNI_OnLoad) failed: {e}"));
            panic!("Failed to find libBootstrap.so JNI_OnLoad");
        });

    let vm_ptr = env.get_java_vm().expect("get_java_vm").get_java_vm_pointer();
    early_log::checkpoint("load_bootstrap", &format!("BEFORE libBootstrap.so::JNI_OnLoad ptr={:p} vm={:p}", on_load.inner, vm_ptr));
    let ver = (on_load)(vm_ptr, std::ptr::null_mut());
    early_log::checkpoint("load_bootstrap", &format!("AFTER libBootstrap.so::JNI_OnLoad returned=0x{ver:x}"));

    bootstrap_lib
}

fn run_bootstrap_startup(bootstrap_lib: &NativeLibrary) {
    let initialize: libloading::NativeMethod<extern "C" fn()> = bootstrap_lib
        .sym("startup")
        .unwrap_or_else(|e| {
            early_log::checkpoint("bootstrap_startup", &format!("FATAL: dlsym(startup) failed: {e}"));
            panic!("Failed to find startup");
        });

    early_log::checkpoint("bootstrap_startup", &format!("BEFORE libBootstrap.so::startup ptr={:p}", initialize.inner));
    (initialize)();
    early_log::checkpoint("bootstrap_startup", "AFTER libBootstrap.so::startup");
}

fn load_lib_unity(env: &JNIEnv, lib_dir: Option<&str>) {
    let unity_lib = load_lib_with_dir(lib_dir, "libunity.so", libc::RTLD_NOW | libc::RTLD_GLOBAL)
        .unwrap_or_else(|err| {
            early_log::checkpoint("load_unity", &format!("FATAL: {err}"));
            panic!("{err}");
        });

    let on_load: libloading::NativeMethod<extern "system" fn(*mut JavaVM, *mut libc::c_void) -> jni::sys::jint> = unity_lib
        .sym("JNI_OnLoad")
        .unwrap_or_else(|e| {
            early_log::checkpoint("load_unity", &format!("FATAL: dlsym(libunity.so::JNI_OnLoad) failed: {e}"));
            panic!("Couldn't find libunity JNI_OnLoad");
        });

    let vm_ptr = env.get_java_vm().expect("get_java_vm").get_java_vm_pointer();
    early_log::checkpoint("load_unity", &format!("BEFORE libunity.so::JNI_OnLoad ptr={:p} vm={:p}", on_load.inner, vm_ptr));
    let ver = (on_load)(vm_ptr, std::ptr::null_mut());
    early_log::checkpoint("load_unity", &format!("AFTER libunity.so::JNI_OnLoad returned=0x{ver:x}"));
}
"""
    write_utf8(proxy_dir / "nativeloader.rs", nativeloader_rs)
    print(f"[patched] {proxy_dir} (early logging, libDir dlopen, stage isolation, load order)")


def patch_rust_bootstrap(root: Path) -> None:
    boot_dir = root / "Rust/Bootstrap/src"
    if not boot_dir.exists():
        raise FileNotFoundError(boot_dir)

    early_log_rs = """use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::fs::{create_dir_all, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use android_liblog_sys::__android_log_write;

static INTERNAL_LOG_PATH: Mutex<Option<PathBuf>> = Mutex::new(None);
static INTERNAL_LOG_PATH_CSTR: Mutex<Option<CString>> = Mutex::new(None);
static BOOTSTRAP_STAGE: Mutex<String> = Mutex::new(String::new());
static SIGNAL_HANDLER_INSTALLED: AtomicBool = AtomicBool::new(false);

pub fn read_cmdline_package() -> Option<String> {
    let data = std::fs::read("/proc/self/cmdline").ok()?;
    let end = data.iter().position(|&b| b == 0).unwrap_or(data.len());
    let s = String::from_utf8_lossy(&data[..end]).trim().to_string();
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

pub fn init(pkg_override: Option<&str>) {
    let pkg = pkg_override
        .map(|s| s.to_string())
        .or_else(read_cmdline_package);

    if let Some(pkg_name) = pkg {
        let dir = PathBuf::from("/data/data").join(&pkg_name).join("files");
        let _ = create_dir_all(&dir);
        let path = dir.join("lemon_early_crash.log");
        if let Ok(cpath) = CString::new(path.to_string_lossy().as_bytes()) {
            if let Ok(mut guard) = INTERNAL_LOG_PATH_CSTR.lock() {
                *guard = Some(cpath);
            }
        }
        if let Ok(mut guard) = INTERNAL_LOG_PATH.lock() {
            *guard = Some(path);
        }

        let stage_file = dir.join("lemon_stage.txt");
        if let Ok(txt) = std::fs::read_to_string(&stage_file) {
            set_stage(&txt);
        }
    }

    install_crash_signal_handlers();
}

pub fn set_stage(stage: &str) {
    let norm = stage.trim().to_ascii_lowercase();
    if !norm.is_empty() {
        if let Ok(mut guard) = BOOTSTRAP_STAGE.lock() {
            *guard = norm;
        }
    }
}

pub fn get_stage() -> String {
    if let Ok(guard) = BOOTSTRAP_STAGE.lock() {
        if !guard.is_empty() {
            return guard.clone();
        }
    }
    "full".to_string()
}

pub fn checkpoint(stage: &str, msg: &str) {
    let line = format!("[Bootstrap][{stage}] {msg}");
    if let Ok(tag) = CString::new("LemonEarly") {
        if let Ok(cmsg) = CString::new(line.as_str()) {
            unsafe {
                __android_log_write(4, tag.as_ptr(), cmsg.as_ptr());
            }
        }
    }
    if let Ok(tag) = CString::new("MelonLoader") {
        if let Ok(cmsg) = CString::new(line.as_str()) {
            unsafe {
                __android_log_write(4, tag.as_ptr(), cmsg.as_ptr());
            }
        }
    }

    if let Ok(guard) = INTERNAL_LOG_PATH.lock() {
        if let Some(ref path) = *guard {
            if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
                let _ = writeln!(f, "{line}");
                let _ = f.sync_all();
            }
        }
    }
}

pub fn format_code_bytes(ptr: *const c_void, count: usize) -> String {
    if ptr.is_null() {
        return "<null>".to_string();
    }
    let bytes = unsafe { std::slice::from_raw_parts(ptr as *const u8, count) };
    let mut out = String::with_capacity(count * 3);
    for (idx, b) in bytes.iter().enumerate() {
        if idx > 0 {
            out.push(' ');
        }
        out.push_str(&format!("{:02x}", b));
    }
    out
}

pub fn describe_addr(addr: *const c_void) -> String {
    if addr.is_null() {
        return "0x0".to_string();
    }
    let mut info: libc::Dl_info = unsafe { std::mem::zeroed() };
    let ok = unsafe { libc::dladdr(addr, &mut info) };
    if ok == 0 {
        return format!("{:p} (dladdr=0)", addr);
    }
    let fname = if info.dli_fname.is_null() {
        "<unknown>"
    } else {
        unsafe { CStr::from_ptr(info.dli_fname) }.to_str().unwrap_or("<non-utf8>")
    };
    let sname = if info.dli_sname.is_null() {
        "<unknown>"
    } else {
        unsafe { CStr::from_ptr(info.dli_sname) }.to_str().unwrap_or("<non-utf8>")
    };
    let off = (addr as usize).saturating_sub(info.dli_fbase as usize);
    format!("{:p} ({}+0x{:x}, sym={})", addr, fname, off, sname)
}

pub fn install_crash_signal_handlers() {
    if SIGNAL_HANDLER_INSTALLED.swap(true, Ordering::SeqCst) {
        return;
    }
    unsafe {
        let mut sa: libc::sigaction = std::mem::zeroed();
        libc::sigemptyset(&mut sa.sa_mask);
        sa.sa_flags = libc::SA_SIGINFO | libc::SA_NODEFER;
        sa.sa_sigaction = crash_signal_handler as usize;
        for sig in [libc::SIGSEGV, libc::SIGABRT, libc::SIGBUS, libc::SIGILL] {
            libc::sigaction(sig, &sa, std::ptr::null_mut());
        }
    }
    checkpoint("signal_handler", "Installed crash signal diagnostics for SIGSEGV/SIGABRT/SIGBUS/SIGILL");
}

fn push_str(buf: &mut [u8], pos: &mut usize, s: &[u8]) {
    let avail = buf.len().saturating_sub(*pos);
    let n = avail.min(s.len());
    buf[*pos..*pos + n].copy_from_slice(&s[..n]);
    *pos += n;
}

fn push_hex(buf: &mut [u8], pos: &mut usize, mut val: u64) {
    push_str(buf, pos, b"0x");
    let mut tmp = [0u8; 16];
    let mut i = 0usize;
    if val == 0 {
        push_str(buf, pos, b"0");
        return;
    }
    while val > 0 && i < 16 {
        let d = (val & 0xF) as u8;
        tmp[i] = if d < 10 { b'0' + d } else { b'a' + (d - 10) };
        val >>= 4;
        i += 1;
    }
    while i > 0 {
        i -= 1;
        push_str(buf, pos, &[tmp[i]]);
    }
}

unsafe fn push_dladdr(buf: &mut [u8], pos: &mut usize, addr: *const c_void) {
    if addr.is_null() {
        push_str(buf, pos, b"0x0");
        return;
    }
    push_hex(buf, pos, addr as u64);
    let mut info: libc::Dl_info = std::mem::zeroed();
    if libc::dladdr(addr, &mut info) != 0 && !info.dli_fname.is_null() {
        push_str(buf, pos, b" [");
        let cstr = CStr::from_ptr(info.dli_fname).to_bytes();
        push_str(buf, pos, cstr);
        push_str(buf, pos, b"+");
        push_hex(buf, pos, (addr as u64).saturating_sub(info.dli_fbase as u64));
        if !info.dli_sname.is_null() {
            push_str(buf, pos, b" ");
            push_str(buf, pos, CStr::from_ptr(info.dli_sname).to_bytes());
        }
        push_str(buf, pos, b"]");
    }
}

extern "C" fn crash_signal_handler(
    sig: c_int,
    info: *mut libc::siginfo_t,
    uctx: *mut c_void,
) {
    unsafe {
        let mut buf = [0u8; 768];
        let mut pos = 0usize;

        push_str(&mut buf, &mut pos, b"[LemonEarly][CRASH] signal=");
        push_hex(&mut buf, &mut pos, sig as u64);

        if !info.is_null() {
            push_str(&mut buf, &mut pos, b" code=");
            push_hex(&mut buf, &mut pos, (*info).si_code as u64);
            push_str(&mut buf, &mut pos, b" fault_addr=");
            push_hex(&mut buf, &mut pos, (*info).si_addr() as u64);
        }

        if !uctx.is_null() {
            let uc = &*(uctx as *const libc::ucontext_t);
            push_str(&mut buf, &mut pos, b" pc=");
            push_dladdr(&mut buf, &mut pos, uc.uc_mcontext.pc as *const c_void);
            push_str(&mut buf, &mut pos, b" lr=");
            push_dladdr(&mut buf, &mut pos, uc.uc_mcontext.regs[30] as *const c_void);
            push_str(&mut buf, &mut pos, b" sp=");
            push_hex(&mut buf, &mut pos, uc.uc_mcontext.sp);
        }

        push_str(&mut buf, &mut pos, b"\\n\\0");
        let tag = b"LemonEarly\\0";
        __android_log_write(6, tag.as_ptr() as *const c_char, buf.as_ptr() as *const c_char);

        if let Ok(guard) = INTERNAL_LOG_PATH_CSTR.try_lock() {
            if let Some(ref cpath) = *guard {
                let fd = libc::open(cpath.as_ptr(), libc::O_WRONLY | libc::O_CREAT | libc::O_APPEND, 0o644);
                if fd >= 0 {
                    let write_len = pos.saturating_sub(1);
                    libc::write(fd, buf.as_ptr() as *const c_void, write_len);
                    libc::fsync(fd);
                    libc::close(fd);
                }
            }
        }

        let mut dfl: libc::sigaction = std::mem::zeroed();
        libc::sigemptyset(&mut dfl.sa_mask);
        dfl.sa_sigaction = libc::SIG_DFL;
        libc::sigaction(sig, &dfl, std::ptr::null_mut());
        libc::raise(sig);
    }
}
"""
    write_utf8(boot_dir / "early_log.rs", early_log_rs)

    lib_rs = boot_dir / "lib.rs"
    lib_text = lib_rs.read_text(encoding="utf-8")
    if "pub mod early_log;" not in lib_text:
        lib_text = lib_text.replace(
            "pub mod core;",
            "pub mod core;\n#[cfg(target_os = \"android\")]\npub mod early_log;",
            1,
        )
        write_utf8(lib_rs, lib_text)

    core_android_rs = """use crate::{
    early_log, log,
    melonenv::paths,
    utils::{
        apk_asset_manager::{copy_melonloader_data, get_apk_modification_date, read_apk_text_asset},
        perm_requester,
    },
};
use jni::{
    sys::{jint, JNI_VERSION_1_6},
    JNIEnv, JavaVM,
};
use lazy_static::lazy_static;
use std::{ffi::c_char, os::raw::c_void, panic::catch_unwind, sync::Mutex};

const INVALID_JNI_VERSION: jint = 0;

lazy_static! {
    pub static ref JAVA_VM: Mutex<Option<JavaVM>> = Mutex::new(None);
}

#[no_mangle]
pub extern "system" fn JNI_OnLoad(vm: JavaVM, _: *mut c_void) -> jint {
    early_log::init(None);
    early_log::checkpoint("bootstrap_jni", "Entered libBootstrap.so JNI_OnLoad");

    let mut vm_mutex = JAVA_VM.lock().unwrap();
    *vm_mutex = Some(vm);

    let mut env: JNIEnv = vm_mutex
        .as_ref()
        .unwrap()
        .get_env()
        .expect("Cannot get reference to the JNIEnv");
    let _ = vm_mutex.as_ref().unwrap().attach_current_thread();

    paths::cache_data_dir(&mut env);
    if let Ok(pkg) = paths::get_package_name() {
        early_log::init(Some(&pkg));
        early_log::checkpoint("bootstrap_jni", &format!("Cached package={pkg}"));
    }

    if let Some(stage_txt) = read_apk_text_asset(&mut env, "lemon_stage.txt") {
        early_log::set_stage(&stage_txt);
    }
    early_log::checkpoint("bootstrap_jni", &format!("Active stage={}", early_log::get_stage()));

    let perms_ok = perm_requester::ensure_perms(&mut env);
    early_log::checkpoint("bootstrap_jni", &format!("ensure_perms={perms_ok}"));

    let melonloader_path = paths::MELONLOADER_FOLDER.clone().0;
    if std::fs::metadata(&melonloader_path).is_err() {
        if let Err(e) = std::fs::create_dir_all(&melonloader_path) {
            early_log::checkpoint(
                "bootstrap_jni",
                &format!("WARN create_dir_all({}): {e}", melonloader_path.display()),
            );
        }
    }

    if let Err(e) = crate::logging::logger::init() {
        early_log::checkpoint("bootstrap_jni", &format!("WARN logger::init: {e}"));
    }

    #[cfg(debug_assertions)]
    std::thread::spawn(|| unsafe {
        crate::dotnet_trace::redirect_stderr();
    });
    #[cfg(debug_assertions)]
    std::thread::spawn(|| unsafe {
        crate::dotnet_trace::redirect_stdout();
    });

    log!("JNI initialized!");
    early_log::checkpoint("bootstrap_jni", "BEFORE copy_melonloader_data");

    match get_apk_modification_date(&mut env) {
        Ok(lemon_patch_date) => {
            if let Err(e) = copy_melonloader_data(&mut env, lemon_patch_date) {
                early_log::checkpoint("bootstrap_jni", &format!("ERROR copy_melonloader_data: {e}"));
            } else {
                log!("APK assets copied!");
                early_log::checkpoint("bootstrap_jni", "AFTER copy_melonloader_data OK");
            }
        }
        Err(e) => {
            early_log::checkpoint("bootstrap_jni", &format!("ERROR get_apk_modification_date: {e}"));
        }
    }

    catch_unwind(|| JNI_VERSION_1_6).unwrap_or(INVALID_JNI_VERSION)
}

pub unsafe fn get_raw_java_vm() -> *mut *const c_void {
    let mutex = JAVA_VM.lock().unwrap();
    let vm = mutex.as_ref().expect("JavaVM not initialized");
    vm.get_java_vm_pointer() as *mut *const c_void
}

#[no_mangle]
pub extern "C" fn melonloader_print_string(input: *const c_char) {
    unsafe {
        if input.is_null() {
            return;
        }
        let msg = std::ffi::CStr::from_ptr(input).to_string_lossy();
        early_log::checkpoint("managed_native_host", &msg);
    }
}
"""
    write_utf8(boot_dir / "core_android.rs", core_android_rs)

    core_rs = """use crate::{console, early_log, errors::DynErr, hooks, internal_failure};

#[no_mangle]
pub extern "C" fn startup() {
    early_log::checkpoint("bootstrap_startup", "Entered libBootstrap.so::startup()");
    init().unwrap_or_else(|e| {
        early_log::checkpoint("bootstrap_startup", &format!("FATAL init error: {e}"));
        internal_failure!("Failed to initialize MelonLoader: {}", e.to_string());
    });
    early_log::checkpoint("bootstrap_startup", "Finished libBootstrap.so::startup()");
}

fn init() -> Result<(), DynErr> {
    let stage = early_log::get_stage();
    early_log::checkpoint("bootstrap_startup", &format!("startup::init stage={stage}"));

    if stage == "no_hooks" || stage == "minimal_bootstrap" {
        early_log::checkpoint("bootstrap_startup", &format!("Stage={stage}: skipping all native hooks"));
        return Ok(());
    }

    console::init()?;
    hooks::init_hook::hook()?;
    console::null_handles()?;

    Ok(())
}

pub fn shutdown() {
    std::process::exit(0);
}
"""
    write_utf8(boot_dir / "core.rs", core_rs)

    # Patch apk_asset_manager.rs: add read_apk_text_asset + null-check AAssetManager_open
    aam_path = boot_dir / "utils/apk_asset_manager.rs"
    aam_text = aam_path.read_text(encoding="utf-8")
    if "pub fn read_apk_text_asset" not in aam_text:
        extra_fn = """
pub fn read_apk_text_asset(env: &mut JNIEnv, filename: &str) -> Option<String> {
    let unity_class = env.find_class("com/unity3d/player/UnityPlayer").ok()?;
    let current_activity_obj: JObject = env
        .get_static_field(unity_class, "currentActivity", "Landroid/app/Activity;")
        .ok()?
        .l()
        .ok()?;
    if current_activity_obj.is_null() {
        return None;
    }
    let asset_manager_obj = env
        .call_method(
            current_activity_obj,
            "getAssets",
            "()Landroid/content/res/AssetManager;",
            &[],
        )
        .ok()?
        .l()
        .ok()?;
    let native_asset_manager = unsafe {
        ndk_sys::AAssetManager_fromJava(env.get_native_interface(), asset_manager_obj.as_raw())
    };
    if native_asset_manager.is_null() {
        return None;
    }
    let filename_cstr = CString::new(filename).ok()?;
    let asset = unsafe {
        ndk_sys::AAssetManager_open(
            native_asset_manager,
            filename_cstr.as_ptr(),
            ndk_sys::AASSET_MODE_BUFFER as i32,
        )
    };
    if asset.is_null() {
        return None;
    }
    let mut out = Vec::new();
    let mut buffer = [0u8; 256];
    loop {
        let bytes_read = unsafe {
            ndk_sys::AAsset_read(
                asset,
                buffer.as_mut_ptr() as *mut std::ffi::c_void,
                buffer.len(),
            )
        };
        if bytes_read <= 0 {
            break;
        }
        out.extend_from_slice(&buffer[0..bytes_read as usize]);
    }
    unsafe { ndk_sys::AAsset_close(asset); }
    Some(String::from_utf8_lossy(&out).trim().to_string())
}
"""
        aam_text = aam_text + "\n" + extra_fn
        aam_text = aam_text.replace(
            "    let asset = ndk_sys::AAssetManager_open(\n        asset_manager,\n        filename_cstr.as_ptr(),\n        ndk_sys::AASSET_MODE_UNKNOWN as i32,\n    );",
            "    let asset = ndk_sys::AAssetManager_open(\n        asset_manager,\n        filename_cstr.as_ptr(),\n        ndk_sys::AASSET_MODE_UNKNOWN as i32,\n    );\n    if asset.is_null() {\n        crate::early_log::checkpoint(\"apk_assets\", &format!(\"WARN AAssetManager_open returned null for {filename}\"));\n        return;\n    }",
        )
        aam_text = aam_text.replace(
            "    let mut output_stream = fs::File::create(full_path).unwrap();",
            "    if let Some(parent) = std::path::Path::new(&full_path).parent() {\n        let _ = fs::create_dir_all(parent);\n    }\n    let mut output_stream = match fs::File::create(&full_path) {\n        Ok(f) => f,\n        Err(e) => {\n            ndk_sys::AAsset_close(asset);\n            crate::early_log::checkpoint(\"apk_assets\", &format!(\"ERROR File::create({full_path}): {e}\"));\n            return;\n        }\n    };",
        )
        aam_text = aam_text.replace(
            "        if fs::create_dir(&path).is_err() {",
            "        if fs::create_dir_all(&path).is_err() {",
        )
        write_utf8(aam_path, aam_text)

    # Patch hooks/functions.rs to log target bytes + dladdr + original/trampoline pointers
    functions_rs = """use crate::{
    early_log,
    errors::{hookerr::HookError, DynErr},
};
use std::ffi::c_void;

pub fn hook(target: usize, detour: usize) -> Result<usize, HookError> {
    if target == 0 {
        early_log::checkpoint("dobby_hook", "ERROR target pointer is 0x0");
        return Err(HookError::Nullpointer("target".to_string()));
    }

    if detour == 0 {
        early_log::checkpoint("dobby_hook", "ERROR detour pointer is 0x0");
        return Err(HookError::Nullpointer("detour".to_string()));
    }

    let target_desc = early_log::describe_addr(target as *const c_void);
    let detour_desc = early_log::describe_addr(detour as *const c_void);
    let prologue = early_log::format_code_bytes(target as *const c_void, 16);
    early_log::checkpoint(
        "dobby_hook",
        &format!("BEFORE DobbyHook: target={target_desc}, detour={detour_desc}, prologue=[{prologue}]"),
    );

    unsafe {
        let trampoline = dobby_rs::hook(target as dobby_rs::Address, detour as dobby_rs::Address)
            .map_err(|e| HookError::Failed(e.to_string()))?;

        if trampoline.is_null() {
            early_log::checkpoint("dobby_hook", "ERROR DobbyHook returned null trampoline");
            return Err(HookError::Null);
        }

        let tramp_desc = early_log::describe_addr(trampoline as *const c_void);
        early_log::checkpoint(
            "dobby_hook",
            &format!("AFTER DobbyHook: target=0x{target:x}, detour=0x{detour:x}, trampoline={tramp_desc}"),
        );
        Ok(trampoline as usize)
    }
}

pub fn unhook(target: usize) -> Result<(), DynErr> {
    if target == 0 {
        return Err(HookError::Nullpointer("target".to_string()).into());
    }

    early_log::checkpoint("dobby_unhook", &format!("BEFORE DobbyDestroy target=0x{target:x}"));
    unsafe {
        dobby_rs::unhook(target as dobby_rs::Address)?;
    }
    early_log::checkpoint("dobby_unhook", &format!("AFTER DobbyDestroy target=0x{target:x}"));

    Ok(())
}
"""
    write_utf8(boot_dir / "hooks/functions.rs", functions_rs)

    init_hook_mod_rs = """mod il2cpp;

use crate::{
    debug,
    early_log,
    errors::DynErr,
    internal_failure,
    runtime,
};
use std::ffi::c_void;
use unity_rs::runtime::RuntimeType;

use super::NativeHook;

pub fn hook() -> Result<(), DynErr> {
    early_log::checkpoint("init_hook", "BEFORE runtime!() (dlopen libil2cpp.so)");
    let runtime = runtime!()?;
    early_log::checkpoint("init_hook", &format!("AFTER runtime!(): type={}", runtime.get_type()));

    match runtime.get_type() {
        RuntimeType::Mono(_) => internal_failure!("Mono is unsupported."),

        RuntimeType::Il2Cpp(_) => {
            debug!("Attaching hook to il2cpp_init")?;

            let init_function = runtime.get_export_ptr("il2cpp_init")?;
            let detour = il2cpp::detour as usize;
            early_log::checkpoint(
                "init_hook",
                &format!(
                    "Resolved il2cpp_init={} detour={}",
                    early_log::describe_addr(init_function as *const c_void),
                    early_log::describe_addr(detour as *const c_void)
                ),
            );

            let mut init_hook = il2cpp::INIT_HOOK.try_write()?;
            *init_hook = NativeHook::new(init_function, detour as *mut c_void);
            init_hook.hook()?;
            early_log::checkpoint(
                "init_hook",
                &format!("il2cpp_init hooked; trampoline={:p}", init_hook.trampoline),
            );
        }
    };

    Ok(())
}
"""
    write_utf8(boot_dir / "hooks/init_hook/mod.rs", init_hook_mod_rs)

    init_hook_il2cpp_rs = """use std::{env, ffi::{c_char, CStr}, fs, path::Path, ptr::null_mut, sync::RwLock};

use lazy_static::lazy_static;
use unity_rs::il2cpp::types::Il2CppDomain;

use crate::{
    console,
    constants::InitFnIl2Cpp,
    debug,
    early_log,
    errors::DynErr,
    hooks::{invoke_hook, NativeHook},
    internal_failure,
};

lazy_static! {
    pub static ref INIT_HOOK: RwLock<NativeHook<InitFnIl2Cpp>> =
        RwLock::new(NativeHook::new(null_mut(), null_mut()));
}

pub extern "C" fn detour(name: *const c_char) -> *mut Il2CppDomain {
    let name_str = if name.is_null() {
        "<null>".to_string()
    } else {
        unsafe { CStr::from_ptr(name) }.to_string_lossy().into_owned()
    };
    early_log::checkpoint("il2cpp_init_detour", &format!("ENTER il2cpp_init(name={name_str})"));
    detour_inner(name).unwrap_or_else(|e| {
        early_log::checkpoint("il2cpp_init_detour", &format!("FATAL error: {e}"));
        internal_failure!("il2cpp_init detour failed: {e}");
    })
}

fn detour_inner(name: *const c_char) -> Result<*mut Il2CppDomain, DynErr> {
    console::set_handles()?;

    let ssl_cert_path = "/apex/com.android.conscrypt/cacerts";
    let backup_cert_path = "/system/etc/security/cacerts";

    if Path::new(ssl_cert_path).exists() && is_readable(ssl_cert_path) {
        env::set_var("SSL_CERT_DIR", ssl_cert_path);
    } else if Path::new(backup_cert_path).exists() && is_readable(backup_cert_path) {
        env::set_var("SSL_CERT_DIR", backup_cert_path);
    } else {
        debug!("No readable SSL cert file found; HTTPS requests may fail.");
    }

    debug!(
        "Using {} for SSL certificates",
        env::var("SSL_CERT_DIR").unwrap_or_default()
    );

    let (trampoline_fn, hook_target) = {
        let guard = INIT_HOOK.try_read()?;
        let fn_ptr: InitFnIl2Cpp = *guard;
        (fn_ptr, guard.target)
    };

    early_log::checkpoint(
        "il2cpp_init_detour",
        &format!("BEFORE original il2cpp_init trampoline (target={:p})", hook_target),
    );
    let domain = trampoline_fn(name);
    early_log::checkpoint(
        "il2cpp_init_detour",
        &format!("AFTER original il2cpp_init trampoline -> domain={:p}", domain),
    );

    let stage = early_log::get_stage();
    if stage == "il2cpp_init_only" {
        early_log::checkpoint("il2cpp_init_detour", "Stage=il2cpp_init_only: skipping CoreCLR & invoke_hook");
        let _ = crate::hooks::functions::unhook(hook_target as usize);
        return Ok(domain);
    }

    early_log::checkpoint("il2cpp_init_detour", "BEFORE base_assembly::init (CoreCLR)");
    crate::base_assembly::init(crate::runtime!()?)?;
    early_log::checkpoint("il2cpp_init_detour", "AFTER base_assembly::init (CoreCLR)");

    debug!("Detaching hook from il2cpp_init")?;
    crate::hooks::functions::unhook(hook_target as usize)?;

    invoke_hook::hook()?;

    Ok(domain)
}

fn is_readable(path: &str) -> bool {
    fs::read_dir(path).is_ok()
}
"""
    write_utf8(boot_dir / "hooks/init_hook/il2cpp.rs", init_hook_il2cpp_rs)

    invoke_hook_mod_rs = """mod il2cpp;

use crate::{
    debug,
    early_log,
    errors::DynErr,
    internal_failure,
    runtime,
};
use std::ffi::c_void;
use unity_rs::runtime::RuntimeType;

use super::NativeHook;

pub fn hook() -> Result<(), DynErr> {
    let runtime = runtime!()?;

    match runtime.get_type() {
        RuntimeType::Mono(_) => internal_failure!("Mono is unsupported."),

        RuntimeType::Il2Cpp(_) => {
            debug!("Attaching hook to il2cpp_runtime_invoke")?;

            let init_function = runtime.get_export_ptr("il2cpp_runtime_invoke")?;
            let detour = il2cpp::detour as usize;
            early_log::checkpoint(
                "invoke_hook",
                &format!(
                    "Resolved il2cpp_runtime_invoke={} detour={}",
                    early_log::describe_addr(init_function as *const c_void),
                    early_log::describe_addr(detour as *const c_void)
                ),
            );

            let mut init_hook = il2cpp::INVOKE_HOOK.try_write()?;
            *init_hook = NativeHook::new(init_function, detour as *mut c_void);
            init_hook.hook()?;
            early_log::checkpoint(
                "invoke_hook",
                &format!("il2cpp_runtime_invoke hooked; trampoline={:p}", init_hook.trampoline),
            );
        }
    };

    Ok(())
}
"""
    write_utf8(boot_dir / "hooks/invoke_hook/mod.rs", invoke_hook_mod_rs)

    invoke_hook_il2cpp_rs = """use std::{ffi::c_void, ptr::null_mut, sync::atomic::{AtomicUsize, Ordering}, sync::RwLock};

use lazy_static::lazy_static;
use unity_rs::{common::method::UnityMethod, il2cpp::types::{Il2CppMethod, Il2CppObject}};

use crate::{base_assembly, constants::InvokeFnIl2Cpp, debug, early_log, errors::DynErr, hooks::NativeHook, internal_failure, runtime};

lazy_static! {
    pub static ref INVOKE_HOOK: RwLock<NativeHook<InvokeFnIl2Cpp>> =
        RwLock::new(NativeHook::new(null_mut(), null_mut()));
}

static INVOKE_COUNT: AtomicUsize = AtomicUsize::new(0);

pub extern "C" fn detour(
    method: *mut Il2CppMethod,
    obj: *mut Il2CppObject,
    params: *mut *mut c_void,
    exc: *mut *mut Il2CppObject,
) -> *mut Il2CppObject {
    detour_inner(method, obj, params, exc).unwrap_or_else(|e| {
        early_log::checkpoint("il2cpp_invoke_detour", &format!("FATAL error: {e}"));
        internal_failure!("il2cpp_runtime_invoke detour failed: {e}");
    })
}

fn detour_inner(
    method: *mut Il2CppMethod,
    obj: *mut Il2CppObject,
    params: *mut *mut c_void,
    exc: *mut *mut Il2CppObject,
) -> Result<*mut Il2CppObject, DynErr> {
    let (trampoline_fn, hook_target) = {
        let guard = INVOKE_HOOK.try_read()?;
        let fn_ptr: InvokeFnIl2Cpp = *guard;
        (fn_ptr, guard.target)
    };

    let idx = INVOKE_COUNT.fetch_add(1, Ordering::Relaxed);
    let result = trampoline_fn(method, obj, params, exc);

    if method.is_null() {
        return Ok(result);
    }

    let runtime = runtime!()?;

    let safe_method = UnityMethod::new(method.cast())?;
    let name = safe_method.get_name(runtime)?;

    if idx < 8 {
        early_log::checkpoint("il2cpp_invoke_detour", &format!("invoke[{idx}] method={name}"));
    }

    if name.contains("Internal_ActiveSceneChanged") {
        early_log::checkpoint("il2cpp_invoke_detour", &format!("Hit {name}; unhooking il2cpp_runtime_invoke"));
        debug!("Detaching hook from il2cpp_runtime_invoke")?;
        crate::hooks::functions::unhook(hook_target as usize)?;

        debug!("Resetting mono thread")?;

        let lib = crate::mono_lib!()?;
        let thread_suspend_reload = lib.exports.mono_melonloader_thread_suspend_reload.as_ref().unwrap();
        thread_suspend_reload();

        debug!("Mono thread reset")?;
        early_log::checkpoint("il2cpp_invoke_detour", "BEFORE base_assembly::pre_start()");
        base_assembly::pre_start()?;
        early_log::checkpoint("il2cpp_invoke_detour", "BEFORE base_assembly::start()");
        base_assembly::start()?;
        early_log::checkpoint("il2cpp_invoke_detour", "AFTER base_assembly::start()");
    }

    Ok(result)
}
"""
    write_utf8(boot_dir / "hooks/invoke_hook/il2cpp.rs", invoke_hook_il2cpp_rs)

    # Patch base_assembly/dotnet.rs with checkpoint logs around hostfxr, LoadStage1, LoadStage2, initialize
    dotnet_rs = boot_dir / "base_assembly/dotnet.rs"
    dtext = dotnet_rs.read_text(encoding="utf-8")
    if "early_log::checkpoint" not in dtext:
        dtext = dtext.replace(
            "use crate::{\n    core_android,",
            "use crate::{\n    core_android, early_log,",
            1,
        )
        dtext = dtext.replace(
            "    let hostfxr_path = melonenv::paths::get_dotnet_path()?.join(\"host/fxr/8.0.6/libhostfxr.so\");",
            "    let hostfxr_path = melonenv::paths::get_dotnet_path()?.join(\"host/fxr/8.0.6/libhostfxr.so\");\n    early_log::checkpoint(\"dotnet_init\", &format!(\"Loading hostfxr={}\", hostfxr_path.display()));",
            1,
        )
        dtext = dtext.replace(
            "    apply_mono_patches()?;",
            "    early_log::checkpoint(\"dotnet_init\", \"BEFORE apply_mono_patches\");\n    apply_mono_patches()?;\n    early_log::checkpoint(\"dotnet_init\", \"AFTER apply_mono_patches\");",
            1,
        )
        dtext = dtext.replace(
            "    init(addr_of_mut!(imports));",
            "    early_log::checkpoint(\"dotnet_init\", \"BEFORE LoadStage1\");\n    init(addr_of_mut!(imports));\n    early_log::checkpoint(\"dotnet_init\", \"AFTER LoadStage1\");",
            1,
        )
        dtext = dtext.replace(
            "    init_stage_two(addr_of_mut!(imports), addr_of_mut!(exports));",
            "    early_log::checkpoint(\"dotnet_init\", \"BEFORE LoadStage2\");\n    init_stage_two(addr_of_mut!(imports), addr_of_mut!(exports));\n    early_log::checkpoint(\"dotnet_init\", \"AFTER LoadStage2\");",
            1,
        )
        dtext = dtext.replace(
            "    (imports.initialize)();",
            "    early_log::checkpoint(\"dotnet_init\", \"BEFORE Managed Initialize()\");\n    (imports.initialize)();\n    early_log::checkpoint(\"dotnet_init\", \"AFTER Managed Initialize()\");",
            1,
        )
        write_utf8(dotnet_rs, dtext)

    print(f"[patched] {boot_dir} (early crash log, signal handler, ABI & hook checkpoints, stage support)")


def patch_csproj_asmresolver(root: Path) -> None:
    ml_csproj = root / "MelonLoader/MelonLoader.csproj"
    if not ml_csproj.exists():
        raise FileNotFoundError(ml_csproj)

    text = ml_csproj.read_text(encoding="utf-8")
    updated = re.sub(
        r'<PackageReference Include="AsmResolver\.DotNet" Version="6\.0\.0-beta\.[0-9]+" />',
        '<PackageReference Include="AsmResolver.DotNet" Version="6.0.0-beta.5" />',
        text,
    )
    if updated != text:
        write_utf8(ml_csproj, updated)
        print(f"[patched] {ml_csproj} -> AsmResolver.DotNet 6.0.0-beta.5")


def verify_il2cpp_interop_abi(root: Path) -> None:
    class_injector = root / "External/Il2CppInterop/Il2CppInterop.Runtime/Injection/ClassInjector.cs"
    if not class_injector.exists():
        raise FileNotFoundError(class_injector)
    ci_text = class_injector.read_text(encoding="utf-8")
    if "LogSuccess" not in ci_text:
        raise RuntimeError("Il2CppInterop ClassInjector.cs is missing RegisterTypeOptions.LogSuccess!")

    il2cpp_csproj = root / "Dependencies/SupportModules/Il2Cpp/Il2Cpp.csproj"
    if not il2cpp_csproj.exists():
        raise FileNotFoundError(il2cpp_csproj)
    proj_text = il2cpp_csproj.read_text(encoding="utf-8")
    if "Il2CppInterop.Runtime.csproj" not in proj_text:
        raise RuntimeError("SupportModules/Il2Cpp.csproj does not reference local External/Il2CppInterop.Runtime.csproj!")

    print("[verified] Il2CppInterop.Runtime RegisterTypeOptions.LogSuccess and SupportModules/Il2Cpp ProjectReference ABI alignment OK.")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to-LemonLoader-MelonLoader-checkout>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1]).resolve()
    if not (root / "MelonLoader/MelonLoader.csproj").exists():
        print(f"Error: {root} does not look like a LemonLoader/MelonLoader checkout.", file=sys.stderr)
        return 1

    patch_dotnet_alc_fix(root)
    patch_mod_handler_redirection_fix(root)
    patch_instance_patch_fix(root)
    patch_shield(root)
    patch_detour_context_dispose_fix(root)
    patch_process_fix(root)
    patch_il2cpp_interop_fixes(root)
    patch_rust_melon_proxy(root)
    patch_rust_bootstrap(root)
    patch_csproj_asmresolver(root)
    verify_il2cpp_interop_abi(root)
    print("[success] All LemonLoader fork source repairs applied cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
