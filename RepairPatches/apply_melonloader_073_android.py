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

    # 3. ClrMonoLib.cs (patched with explicit Cdecl calling conventions + runtime version fallback)
    clr_mono_dst = root / "MelonLoader.Bootstrap/RuntimeHandlers/Il2Cpp/ClrMonoLib.cs"
    clr_mono_text = (lemon_070 / "MelonLoader.Bootstrap/RuntimeHandlers/Il2Cpp/ClrMonoLib.cs").read_text(encoding="utf-8")
    clr_mono_text = clr_mono_text.replace(
        "public delegate bool CheckThreadDelegate(ulong threadId);",
        "[UnmanagedFunctionPointer(CallingConvention.Cdecl)]\n"
        "    [return: MarshalAs(UnmanagedType.U1)]\n"
        "    public delegate bool CheckThreadDelegate(ulong threadId);",
    )
    clr_mono_text = clr_mono_text.replace(
        "public delegate void MonoUnhandledExceptionDelegate(IntPtr exc, IntPtr userData);",
        "[UnmanagedFunctionPointer(CallingConvention.Cdecl)]\n"
        "    public delegate void MonoUnhandledExceptionDelegate(IntPtr exc, IntPtr userData);",
    )
    clr_mono_text = replace_once(
        clr_mono_text,
        "        if (!NativeLibrary.TryLoad(Path.Combine(AndroidBootstrap.DotnetDir, \"shared\", \"Microsoft.NETCore.App\", \"8.0.6\", \"libcoreclr.so\"), out var hRuntime)",
        "        string sharedRoot = Path.Combine(AndroidBootstrap.DotnetDir, \"shared\", \"Microsoft.NETCore.App\");\n"
        "        string libPath = Path.Combine(sharedRoot, \"8.0.6\", \"libcoreclr.so\");\n"
        "        if (!File.Exists(libPath) && Directory.Exists(sharedRoot))\n"
        "        {\n"
        "            libPath = Directory.GetDirectories(sharedRoot)\n"
        "                .Select(d => Path.Combine(d, \"libcoreclr.so\"))\n"
        "                .FirstOrDefault(File.Exists) ?? libPath;\n"
        "        }\n\n"
        "        if (!NativeLibrary.TryLoad(libPath, out var hRuntime)",
        "ClrMonoLib.cs dynamic libcoreclr.so path",
    )
    write_utf8(clr_mono_dst, clr_mono_text)

    print("[copied] LemonLoader 0.7.0 JNI, APKAssetManager, linux-bionic-arm64 deps, and ClrMonoLib")


def patch_lemon070_jni_and_asset_manager(root: Path) -> None:
    # Fix 0: JavaVM.cs AttachCurrentThread arg 3 was `ref IntPtr` (passing &refPtr = non-null bogus
    # JavaVMAttachArgs* with version=0!), causing ART to return JNI_EVERSION (-3) and leave env=null!
    jvm_path = root / "MelonLoader/JNI/JavaVM.cs"
    jvm_text = jvm_path.read_text(encoding="utf-8")
    jvm_text = replace_once(
        jvm_text,
        "        internal readonly delegate* unmanaged[Stdcall]<JavaVM*, out JNIEnv*, ref IntPtr, JNI.Result> AttachCurrentThread;",
        "        internal readonly delegate* unmanaged[Stdcall]<JavaVM*, out JNIEnv*, IntPtr, JNI.Result> AttachCurrentThread;",
        "JavaVM.cs fix AttachCurrentThread args pointer signature",
    )
    write_utf8(jvm_path, jvm_text)

    # Fix 0b: JValue.cs guard null JObject
    jval_path = root / "MelonLoader/JNI/JValue.cs"
    jval_text = jval_path.read_text(encoding="utf-8")
    jval_text = replace_once(
        jval_text,
        "    public JValue(JObject obj)\n    {\n        this = new JValue();\n        this.L = obj.Handle;\n    }",
        "    public JValue(JObject? obj)\n    {\n        this = new JValue();\n        this.L = obj?.Handle ?? IntPtr.Zero;\n    }",
        "JValue.cs guard null JObject",
    )
    write_utf8(jval_path, jval_text)

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

    # Fix 2: JNI.cs
    # - Fix Initialize: pass IntPtr.Zero (NULL) to AttachCurrentThread, use AttachCurrentThreadAsDaemon
    #   on background/finalizer threads, and check Result == 0
    # - Guard Env == null in DeleteGlobalRef/DeleteWeakGlobalRef/DeleteLocalRef/NewGlobalRef/NewLocalRef/ExceptionCheck/ExceptionClear
    # - Fix FindClass thread-safety + return LocalRef
    # - Fix GetJStringString (ReleaseStringUTFChars + PtrToStringUTF8)
    # - Fix GetStaticMethodID HGlobal leak, NewObject Console.WriteLine + local ref leak, NewArray long/float/double
    # - Pin args arrays with `fixed (JValue* argsPtr = args)` instead of unpinned UnsafeAddrOfPinnedArrayElement
    jni_path = root / "MelonLoader/JNI/JNI.cs"
    jni_text = jni_path.read_text(encoding="utf-8")
    jni_text = replace_once(
        jni_text,
        "using System.Collections.Generic;\nusing System.Linq;\nusing System.Runtime.InteropServices;",
        "using System.Collections.Generic;\nusing System.Linq;\nusing System.Runtime.InteropServices;\nusing System.Threading;",
        "JNI.cs using System.Threading",
    )
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
        "            if ((int)res == 0 && envPtr != IntPtr.Zero)\n"
        "            {\n"
        "                env = (JNIEnv*)envPtr;\n"
        "                return;\n"
        "            }\n\n"
        "            if (Thread.CurrentThread.IsBackground && VM->Functions->AttachCurrentThreadAsDaemon != null)\n"
        "            {\n"
        "                res = VM->Functions->AttachCurrentThreadAsDaemon(VM, out IntPtr daemonEnv, IntPtr.Zero);\n"
        "                if ((int)res == 0 && daemonEnv != IntPtr.Zero)\n"
        "                {\n"
        "                    env = (JNIEnv*)daemonEnv;\n"
        "                    return;\n"
        "                }\n"
        "            }\n\n"
        "            res = VM->Functions->AttachCurrentThread(VM, out JNIEnv* localEnv, IntPtr.Zero);\n"
        "            if ((int)res == 0 && localEnv != null)\n"
        "                env = localEnv;\n"
        "        }\n"
        "    }",
        "JNI.cs Initialize null/AttachCurrentThreadAsDaemon/AttachCurrentThread(NULL) fix",
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
        "                if (Env == null)\n"
        "                    return new JClass { Handle = IntPtr.Zero, ReferenceType = JNI.ReferenceType.Local };\n\n"
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
    jni_text = replace_once(
        jni_text,
        "    public static T NewGlobalRef<T>(JObject lobj) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr res = Env->Functions->NewGlobalRef(Env, lobj.Handle);\n"
        "            return new T() { Handle = res, ReferenceType = JNI.ReferenceType.Global };\n"
        "        }\n"
        "    }\n\n"
        "    public static void DeleteGlobalRef(JObject gref)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (gref == null)\n"
        "                return;\n\n"
        "            if (!gref.Valid())\n"
        "                return;\n\n"
        "            Env->Functions->DeleteGlobalRef(Env, gref.Handle);\n"
        "        }\n"
        "    }",
        "    public static T NewGlobalRef<T>(JObject lobj) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (lobj == null || !lobj.Valid() || Env == null)\n"
        "                return new T() { Handle = IntPtr.Zero, ReferenceType = JNI.ReferenceType.Global };\n\n"
        "            IntPtr res = Env->Functions->NewGlobalRef(Env, lobj.Handle);\n"
        "            return new T() { Handle = res, ReferenceType = JNI.ReferenceType.Global };\n"
        "        }\n"
        "    }\n\n"
        "    public static void DeleteGlobalRef(JObject gref)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (gref == null || !gref.Valid() || Env == null)\n"
        "                return;\n\n"
        "            Env->Functions->DeleteGlobalRef(Env, gref.Handle);\n"
        "        }\n"
        "    }",
        "JNI.cs NewGlobalRef + DeleteGlobalRef null guards",
    )
    jni_text = replace_once(
        jni_text,
        "    public static void DeleteLocalRef(JObject lref)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (lref == null)\n"
        "                return;\n\n"
        "            if (!lref.Valid())\n"
        "                return;\n\n"
        "            Env->Functions->DeleteLocalRef(Env, lref.Handle);\n"
        "        }\n"
        "    }",
        "    public static void DeleteLocalRef(JObject lref)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (lref == null || !lref.Valid() || Env == null)\n"
        "                return;\n\n"
        "            Env->Functions->DeleteLocalRef(Env, lref.Handle);\n"
        "        }\n"
        "    }",
        "JNI.cs DeleteLocalRef null guard",
    )
    jni_text = replace_once(
        jni_text,
        "    public static T NewLocalRef<T>(JObject obj) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr res = Env->Functions->NewLocalRef(Env, obj.Handle);\n"
        "            return new T() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "        }\n"
        "    }",
        "    public static T NewLocalRef<T>(JObject obj) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (obj == null || !obj.Valid() || Env == null)\n"
        "                return new T() { Handle = IntPtr.Zero, ReferenceType = JNI.ReferenceType.Local };\n\n"
        "            IntPtr res = Env->Functions->NewLocalRef(Env, obj.Handle);\n"
        "            return new T() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "        }\n"
        "    }",
        "JNI.cs NewLocalRef null guard",
    )
    jni_text = replace_once(
        jni_text,
        "    public static T NewObject<T>(JClass cls, JMethodID methodID, params JValue[] args) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr argsPtr = Marshal.UnsafeAddrOfPinnedArrayElement(args, 0);\n"
        "            IntPtr res = Env->Functions->NewObjectA(Env, cls.Handle, methodID, argsPtr);\n"
        "            Console.WriteLine($\"Res: {res}\");\n"
        "            JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<T>(local);\n"
        "        }\n"
        "    }",
        "    public static T NewObject<T>(JClass cls, JMethodID methodID, params JValue[] args) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            fixed (JValue* v = args)\n"
        "            {\n"
        "                IntPtr res = Env->Functions->NewObjectA(Env, cls.Handle, methodID, (IntPtr)v);\n"
        "                using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "                return NewGlobalRef<T>(local);\n"
        "            }\n"
        "        }\n"
        "    }",
        "JNI.cs NewObject fixed pin + remove Console.WriteLine + using local",
    )
    jni_text = replace_once(
        jni_text,
        "    public static T CallObjectMethod<T>(JObject obj, JMethodID methodID, params JValue[] args) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr argsPtr = Marshal.UnsafeAddrOfPinnedArrayElement(args, 0);\n\n"
        "            fixed (JValue* v = args)\n"
        "            {\n"
        "                IntPtr res = Env->Functions->CallObjectMethodA(Env, obj.Handle, methodID, argsPtr);\n"
        "                using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "                return NewGlobalRef<T>(local);\n"
        "            }\n"
        "        }\n"
        "    }\n\n"
        "    public static T CallMethod<T>(JObject obj, JMethodID methodID, params JValue[] args)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            Type t = typeof(T);\n"
        "            IntPtr argsPtr = Marshal.UnsafeAddrOfPinnedArrayElement(args, 0);",
        "    public static T CallObjectMethod<T>(JObject obj, JMethodID methodID, params JValue[] args) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (obj == null || !obj.Valid() || methodID.Handle == IntPtr.Zero || Env == null)\n"
        "                return new T() { Handle = IntPtr.Zero, ReferenceType = JNI.ReferenceType.Global };\n"
        "            fixed (JValue* v = args)\n"
        "            {\n"
        "                IntPtr res = Env->Functions->CallObjectMethodA(Env, obj.Handle, methodID, (IntPtr)v);\n"
        "                using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "                return NewGlobalRef<T>(local);\n"
        "            }\n"
        "        }\n"
        "    }\n\n"
        "    public static T CallMethod<T>(JObject obj, JMethodID methodID, params JValue[] args)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (obj == null || !obj.Valid() || methodID.Handle == IntPtr.Zero || Env == null)\n"
        "                return default!;\n"
        "            Type t = typeof(T);\n"
        "            fixed (JValue* v = args)\n"
        "            {\n"
        "                IntPtr argsPtr = (IntPtr)v;",
        "JNI.cs CallObjectMethod + CallMethod fixed pin + null guards",
    )
    jni_text = replace_once(
        jni_text,
        "    public static JClass GetObjectClass(JObject obj)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr res = Env->Functions->GetObjectClass(Env, obj.Handle);\n\n"
        "            using JClass local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<JClass>(local);\n"
        "        }\n"
        "    }",
        "    public static JClass GetObjectClass(JObject obj)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (obj == null || !obj.Valid() || Env == null)\n"
        "                return new JClass { Handle = IntPtr.Zero, ReferenceType = JNI.ReferenceType.Global };\n"
        "            IntPtr res = Env->Functions->GetObjectClass(Env, obj.Handle);\n\n"
        "            using JClass local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<JClass>(local);\n"
        "        }\n"
        "    }",
        "JNI.cs GetObjectClass null guard",
    )
    jni_text = replace_once(
        jni_text,
        "    public static JMethodID GetMethodID(JClass cls, string name, string sig)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr nameAnsi = Marshal.StringToHGlobalAnsi(name);\n"
        "            IntPtr sigAnsi = Marshal.StringToHGlobalAnsi(sig);\n\n"
        "            JMethodID id = Env->Functions->GetMethodID(Env, cls.Handle, nameAnsi, sigAnsi);\n\n"
        "            Marshal.FreeHGlobal(nameAnsi);\n"
        "            Marshal.FreeHGlobal(sigAnsi);\n"
        "            return id;\n"
        "        }\n"
        "    }",
        "    public static JMethodID GetMethodID(JClass cls, string name, string sig)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (cls == null || !cls.Valid() || Env == null)\n"
        "                return default;\n"
        "            IntPtr nameAnsi = Marshal.StringToHGlobalAnsi(name);\n"
        "            IntPtr sigAnsi = Marshal.StringToHGlobalAnsi(sig);\n\n"
        "            JMethodID id = Env->Functions->GetMethodID(Env, cls.Handle, nameAnsi, sigAnsi);\n\n"
        "            Marshal.FreeHGlobal(nameAnsi);\n"
        "            Marshal.FreeHGlobal(sigAnsi);\n"
        "            return id;\n"
        "        }\n"
        "    }",
        "JNI.cs GetMethodID null guard",
    )
    jni_text = replace_once(
        jni_text,
        "                throw new ArgumentException($\"CallMethod Type {t} not supported.\");\n"
        "            }\n"
        "        }\n"
        "    }\n\n"
        "    public static void CallVoidMethod(JObject obj, JMethodID methodID, params JValue[] args)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr argsPtr = Marshal.UnsafeAddrOfPinnedArrayElement(args, 0);\n"
        "            Env->Functions->CallVoidMethodA(Env, obj.Handle, methodID, argsPtr);\n"
        "        }\n"
        "    }\n\n"
        "    public static T CallNonvirtualObjectMethod<T>(JObject obj, JClass cls, JMethodID methodID, params JValue[] args) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr argsPtr = Marshal.UnsafeAddrOfPinnedArrayElement(args, 0);\n"
        "            IntPtr res = Env->Functions->CallNonvirtualObjectMethodA(Env, obj.Handle, cls.Handle, methodID, argsPtr);\n\n"
        "            using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<T>(local);\n"
        "        }\n"
        "    }\n\n"
        "    public static T CallNonvirtualMethod<T>(JObject obj, JClass cls, JMethodID methodID, params JValue[] args)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            Type t = typeof(T);\n"
        "            IntPtr argsPtr = Marshal.UnsafeAddrOfPinnedArrayElement(args, 0);",
        "                throw new ArgumentException($\"CallMethod Type {t} not supported.\");\n"
        "            }\n"
        "            }\n"
        "        }\n"
        "    }\n\n"
        "    public static void CallVoidMethod(JObject obj, JMethodID methodID, params JValue[] args)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (obj == null || !obj.Valid() || methodID.Handle == IntPtr.Zero || Env == null)\n"
        "                return;\n"
        "            fixed (JValue* v = args)\n"
        "            {\n"
        "                Env->Functions->CallVoidMethodA(Env, obj.Handle, methodID, (IntPtr)v);\n"
        "            }\n"
        "        }\n"
        "    }\n\n"
        "    public static T CallNonvirtualObjectMethod<T>(JObject obj, JClass cls, JMethodID methodID, params JValue[] args) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            fixed (JValue* v = args)\n"
        "            {\n"
        "                IntPtr res = Env->Functions->CallNonvirtualObjectMethodA(Env, obj.Handle, cls.Handle, methodID, (IntPtr)v);\n"
        "                using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "                return NewGlobalRef<T>(local);\n"
        "            }\n"
        "        }\n"
        "    }\n\n"
        "    public static T CallNonvirtualMethod<T>(JObject obj, JClass cls, JMethodID methodID, params JValue[] args)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            Type t = typeof(T);\n"
        "            fixed (JValue* v = args)\n"
        "            {\n"
        "                IntPtr argsPtr = (IntPtr)v;",
        "JNI.cs CallVoidMethod + CallNonvirtual* fixed pin",
    )
    jni_text = replace_once(
        jni_text,
        "                throw new ArgumentException($\"CallNonvirtualMethod Type {t} not supported.\");\n"
        "            }\n"
        "        }\n"
        "    }\n\n"
        "    public static void CallNonvirtualVoidMethod(JObject obj, JClass cls, JMethodID methodID, params JValue[] args)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr argsPtr = Marshal.UnsafeAddrOfPinnedArrayElement(args, 0);\n"
        "            Env->Functions->CallNonvirtualVoidMethodA(Env, obj.Handle, cls.Handle, methodID, argsPtr);\n"
        "        }\n"
        "    }",
        "                throw new ArgumentException($\"CallNonvirtualMethod Type {t} not supported.\");\n"
        "            }\n"
        "            }\n"
        "        }\n"
        "    }\n\n"
        "    public static void CallNonvirtualVoidMethod(JObject obj, JClass cls, JMethodID methodID, params JValue[] args)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            fixed (JValue* v = args)\n"
        "            {\n"
        "                Env->Functions->CallNonvirtualVoidMethodA(Env, obj.Handle, cls.Handle, methodID, (IntPtr)v);\n"
        "            }\n"
        "        }\n"
        "    }",
        "JNI.cs CallNonvirtualMethod close + CallNonvirtualVoidMethod fixed pin",
    )
    jni_text = replace_once(
        jni_text,
        "    public static JMethodID GetStaticMethodID(JClass cls, string name, string sig)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr nameAnsi = Marshal.StringToHGlobalAnsi(name);\n"
        "            IntPtr sigAnsi = Marshal.StringToHGlobalAnsi(sig);\n"
        "            JMethodID id = Env->Functions->GetStaticMethodID(Env, cls.Handle, nameAnsi, sigAnsi);\n"
        "            return id;\n"
        "        }\n"
        "    }\n\n"
        "    public static T CallStaticObjectMethod<T>(JClass cls, JMethodID methodID, params JValue[] args) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr argsPtr = Marshal.UnsafeAddrOfPinnedArrayElement(args, 0);\n"
        "            IntPtr res = Env->Functions->CallStaticObjectMethodA(Env, cls.Handle, methodID.Handle, argsPtr);\n"
        "            using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<T>(local);\n"
        "        }\n"
        "    }\n\n"
        "    public static T CallStaticMethod<T>(JClass cls, JMethodID methodID, params JValue[] args)\n"
        "    {\n"
        "        Type t = typeof(T);\n"
        "        IntPtr argsPtr = Marshal.UnsafeAddrOfPinnedArrayElement(args, 0);",
        "    public static JMethodID GetStaticMethodID(JClass cls, string name, string sig)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (cls == null || !cls.Valid() || Env == null)\n"
        "                return default;\n"
        "            IntPtr nameAnsi = Marshal.StringToHGlobalAnsi(name);\n"
        "            IntPtr sigAnsi = Marshal.StringToHGlobalAnsi(sig);\n"
        "            JMethodID id = Env->Functions->GetStaticMethodID(Env, cls.Handle, nameAnsi, sigAnsi);\n"
        "            Marshal.FreeHGlobal(nameAnsi);\n"
        "            Marshal.FreeHGlobal(sigAnsi);\n"
        "            return id;\n"
        "        }\n"
        "    }\n\n"
        "    public static T CallStaticObjectMethod<T>(JClass cls, JMethodID methodID, params JValue[] args) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (cls == null || !cls.Valid() || methodID.Handle == IntPtr.Zero || Env == null)\n"
        "                return new T() { Handle = IntPtr.Zero, ReferenceType = JNI.ReferenceType.Global };\n"
        "            fixed (JValue* v = args)\n"
        "            {\n"
        "                IntPtr res = Env->Functions->CallStaticObjectMethodA(Env, cls.Handle, methodID.Handle, (IntPtr)v);\n"
        "                using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "                return NewGlobalRef<T>(local);\n"
        "            }\n"
        "        }\n"
        "    }\n\n"
        "    public static T CallStaticMethod<T>(JClass cls, JMethodID methodID, params JValue[] args)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (cls == null || !cls.Valid() || methodID.Handle == IntPtr.Zero || Env == null)\n"
        "                return default!;\n"
        "        }\n"
        "        Type t = typeof(T);\n"
        "        fixed (JValue* v = args)\n"
        "        {\n"
        "            IntPtr argsPtr = (IntPtr)v;",
        "JNI.cs GetStaticMethodID FreeHGlobal + CallStatic* fixed pin + null guards",
    )
    jni_text = replace_once(
        jni_text,
        "        else\n"
        "        {\n"
        "            throw new ArgumentException($\"CallStaticMethod Type {t} not supported.\");\n"
        "        }\n"
        "    }\n\n"
        "    public static void CallStaticVoidMethod(JClass cls, JMethodID methodID, params JValue[] args)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr argsPtr = Marshal.UnsafeAddrOfPinnedArrayElement(args, 0);\n"
        "            Env->Functions->CallStaticVoidMethodA(Env, cls.Handle, methodID, argsPtr);\n"
        "        }\n"
        "    }",
        "        else\n"
        "        {\n"
        "            throw new ArgumentException($\"CallStaticMethod Type {t} not supported.\");\n"
        "        }\n"
        "        }\n"
        "    }\n\n"
        "    public static void CallStaticVoidMethod(JClass cls, JMethodID methodID, params JValue[] args)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (cls == null || !cls.Valid() || methodID.Handle == IntPtr.Zero || Env == null)\n"
        "                return;\n"
        "            fixed (JValue* v = args)\n"
        "            {\n"
        "                Env->Functions->CallStaticVoidMethodA(Env, cls.Handle, methodID, (IntPtr)v);\n"
        "            }\n"
        "        }\n"
        "    }",
        "JNI.cs CallStaticMethod close + CallStaticVoidMethod fixed pin",
    )
    jni_text = replace_once(
        jni_text,
        "            if (!str.Valid())\n"
        "                return \"\";\n\n"
        "            IntPtr res = Env->Functions->GetStringUTFChars(Env, str.Handle, out byte isCopy);\n"
        "            string? resultString = Marshal.PtrToStringAuto(res);\n"
        "            Env->Functions->ReleaseStringChars(Env, str.Handle, res);",
        "            if (str == null || !str.Valid() || Env == null)\n"
        "                return \"\";\n\n"
        "            IntPtr res = Env->Functions->GetStringUTFChars(Env, str.Handle, out byte isCopy);\n"
        "            string? resultString = res != IntPtr.Zero ? Marshal.PtrToStringUTF8(res) : null;\n"
        "            if (res != IntPtr.Zero)\n"
        "                Env->Functions->ReleaseStringUTFChars(Env, str.Handle, res);",
        "JNI.cs GetJStringString ReleaseStringUTFChars + PtrToStringUTF8 + null guard",
    )
    jni_text = replace_once(
        jni_text,
        "    public static JFieldID GetStaticFieldID(JClass cls, string name, string sig)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr nameAnsi = Marshal.StringToHGlobalAnsi(name);\n"
        "            IntPtr sigAnsi = Marshal.StringToHGlobalAnsi(sig);\n\n"
        "            JFieldID id = Env->Functions->GetStaticFieldID(Env, cls.Handle, nameAnsi, sigAnsi);\n\n"
        "            Marshal.FreeHGlobal(nameAnsi);\n"
        "            Marshal.FreeHGlobal(sigAnsi);\n"
        "            return id;\n"
        "        }\n"
        "    }\n\n"
        "    public static T GetStaticObjectField<T>(JClass cls, JFieldID fieldID) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr res = Env->Functions->GetStaticObjectField(Env, cls.Handle, fieldID);\n\n"
        "            using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<T>(local);\n"
        "        }\n"
        "    }",
        "    public static JFieldID GetStaticFieldID(JClass cls, string name, string sig)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (cls == null || !cls.Valid() || Env == null)\n"
        "                return default;\n"
        "            IntPtr nameAnsi = Marshal.StringToHGlobalAnsi(name);\n"
        "            IntPtr sigAnsi = Marshal.StringToHGlobalAnsi(sig);\n\n"
        "            JFieldID id = Env->Functions->GetStaticFieldID(Env, cls.Handle, nameAnsi, sigAnsi);\n\n"
        "            Marshal.FreeHGlobal(nameAnsi);\n"
        "            Marshal.FreeHGlobal(sigAnsi);\n"
        "            return id;\n"
        "        }\n"
        "    }\n\n"
        "    public static T GetStaticObjectField<T>(JClass cls, JFieldID fieldID) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (cls == null || !cls.Valid() || fieldID.Handle == IntPtr.Zero || Env == null)\n"
        "                return new T() { Handle = IntPtr.Zero, ReferenceType = JNI.ReferenceType.Global };\n"
        "            IntPtr res = Env->Functions->GetStaticObjectField(Env, cls.Handle, fieldID);\n\n"
        "            using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<T>(local);\n"
        "        }\n"
        "    }",
        "JNI.cs GetStaticFieldID + GetStaticObjectField null guards",
    )
    jni_text = replace_once(
        jni_text,
        "    public static JFieldID GetFieldID(JClass cls, string name, string sig)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr nameAnsi = Marshal.StringToHGlobalAnsi(name);\n"
        "            IntPtr sigAnsi = Marshal.StringToHGlobalAnsi(sig);\n\n"
        "            JFieldID id = Env->Functions->GetFieldID(Env, cls.Handle, nameAnsi, sigAnsi);\n\n"
        "            Marshal.FreeHGlobal(nameAnsi);\n"
        "            Marshal.FreeHGlobal(sigAnsi);\n"
        "            return id;\n"
        "        }\n"
        "    }\n\n"
        "    public static T GetObjectField<T>(JObject obj, JFieldID fieldID) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr res = Env->Functions->GetObjectField(Env, obj.Handle, fieldID);\n"
        "            using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<T>(local);\n"
        "        }\n"
        "    }",
        "    public static JFieldID GetFieldID(JClass cls, string name, string sig)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (cls == null || !cls.Valid() || Env == null)\n"
        "                return default;\n"
        "            IntPtr nameAnsi = Marshal.StringToHGlobalAnsi(name);\n"
        "            IntPtr sigAnsi = Marshal.StringToHGlobalAnsi(sig);\n\n"
        "            JFieldID id = Env->Functions->GetFieldID(Env, cls.Handle, nameAnsi, sigAnsi);\n\n"
        "            Marshal.FreeHGlobal(nameAnsi);\n"
        "            Marshal.FreeHGlobal(sigAnsi);\n"
        "            return id;\n"
        "        }\n"
        "    }\n\n"
        "    public static T GetObjectField<T>(JObject obj, JFieldID fieldID) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (obj == null || !obj.Valid() || fieldID.Handle == IntPtr.Zero || Env == null)\n"
        "                return new T() { Handle = IntPtr.Zero, ReferenceType = JNI.ReferenceType.Global };\n"
        "            IntPtr res = Env->Functions->GetObjectField(Env, obj.Handle, fieldID);\n"
        "            using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<T>(local);\n"
        "        }\n"
        "    }",
        "JNI.cs GetFieldID + GetObjectField null guards",
    )
    jni_text = replace_once(
        jni_text,
        "    public static JString NewString(string str)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr strUni = Marshal.StringToHGlobalUni(str);\n\n"
        "            IntPtr res = Env->Functions->NewString(Env, strUni, str.Length);\n\n"
        "            Marshal.FreeHGlobal(strUni);\n\n"
        "            using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<JString>(local);\n"
        "        }\n"
        "    }",
        "    public static JString NewString(string str)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (str == null || Env == null)\n"
        "                return new JString { Handle = IntPtr.Zero, ReferenceType = JNI.ReferenceType.Global };\n"
        "            IntPtr strUni = Marshal.StringToHGlobalUni(str);\n\n"
        "            IntPtr res = Env->Functions->NewString(Env, strUni, str.Length);\n\n"
        "            Marshal.FreeHGlobal(strUni);\n\n"
        "            using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<JString>(local);\n"
        "        }\n"
        "    }",
        "JNI.cs NewString null guard",
    )
    jni_text = replace_once(
        jni_text,
        "    public static int GetArrayLength<T>(JObjectArray<T> jarray) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            return Env->Functions->GetArrayLength(Env, jarray.Handle);\n"
        "        }\n"
        "    }\n\n"
        "    public static T GetObjectArrayElement<T>(JObjectArray<T> array, int index) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            IntPtr res = Env->Functions->GetObjectArrayElement(Env, array.Handle, index);\n\n"
        "            using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<T>(local);\n"
        "        }\n"
        "    }",
        "    public static int GetArrayLength<T>(JObjectArray<T> jarray) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (jarray == null || !jarray.Valid() || Env == null)\n"
        "                return 0;\n"
        "            return Env->Functions->GetArrayLength(Env, jarray.Handle);\n"
        "        }\n"
        "    }\n\n"
        "    public static T GetObjectArrayElement<T>(JObjectArray<T> array, int index) where T : JObject, new()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (array == null || !array.Valid() || Env == null || index < 0)\n"
        "                return new T() { Handle = IntPtr.Zero, ReferenceType = JNI.ReferenceType.Global };\n"
        "            IntPtr res = Env->Functions->GetObjectArrayElement(Env, array.Handle, index);\n\n"
        "            using JObject local = new() { Handle = res, ReferenceType = JNI.ReferenceType.Local };\n"
        "            return NewGlobalRef<T>(local);\n"
        "        }\n"
        "    }",
        "JNI.cs GetArrayLength + GetObjectArrayElement null guards",
    )
    jni_text = replace_once(
        jni_text,
        "            else if (t == typeof(long))\n"
        "            {\n"
        "                res = Env->Functions->NewBooleanArray(Env, length);\n"
        "            }\n"
        "            else if (t == typeof(float))\n"
        "            {\n"
        "                res = Env->Functions->NewBooleanArray(Env, length);\n"
        "            }\n"
        "            else if (t == typeof(double))\n"
        "            {\n"
        "                res = Env->Functions->NewBooleanArray(Env, length);\n"
        "            }",
        "            else if (t == typeof(long))\n"
        "            {\n"
        "                res = Env->Functions->NewLongArray(Env, length);\n"
        "            }\n"
        "            else if (t == typeof(float))\n"
        "            {\n"
        "                res = Env->Functions->NewFloatArray(Env, length);\n"
        "            }\n"
        "            else if (t == typeof(double))\n"
        "            {\n"
        "                res = Env->Functions->NewDoubleArray(Env, length);\n"
        "            }",
        "JNI.cs NewArray long/float/double",
    )
    jni_text = replace_once(
        jni_text,
        "    public static void DeleteWeakGlobalRef(JObject obj)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            Env->Functions->DeleteWeakGlobalRef(Env, obj.Handle);\n"
        "        }\n"
        "    }\n\n"
        "    public static bool ExceptionCheck()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            return Convert.ToBoolean(Env->Functions->ExceptionCheck(Env));\n"
        "        }\n"
        "    }",
        "    public static void DeleteWeakGlobalRef(JObject obj)\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (obj == null || !obj.Valid() || Env == null)\n"
        "                return;\n\n"
        "            Env->Functions->DeleteWeakGlobalRef(Env, obj.Handle);\n"
        "        }\n"
        "    }\n\n"
        "    public static bool ExceptionCheck()\n"
        "    {\n"
        "        unsafe\n"
        "        {\n"
        "            if (Env == null)\n"
        "                return false;\n"
        "            return Convert.ToBoolean(Env->Functions->ExceptionCheck(Env));\n"
        "        }\n"
        "    }",
        "JNI.cs DeleteWeakGlobalRef + ExceptionCheck null guards",
    )
    write_utf8(jni_path, jni_text)

    # Fix 3: APKAssetManager.cs
    # - Fix DoesAssetExist when path has no '/' (e.g. "lemon_stage.txt", "lemon_patch_date.txt")
    # - Clear pending JNI exception BEFORE returning null in GetAssetStream / GetAssetBytes
    # - Null-check assets array in GetDirectoryContents / DoesAssetExist, and dispose each JString immediately
    # - Fix GetAssetBytes JClass local ref leak + use 64KB buffer
    # - Fix APKAssetStream.Read offset bug (pass 0 for javaBuffer offset, copy into buffer[offset..])
    # - Safely obtain Context via UnityPlayer.currentActivity OR ActivityThread.currentApplication()
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
        "            return [];\n\n"
        "        using MemoryStream outputStream = new();\n\n"
        "        using JArray<sbyte> buffer = JNI.NewArray<sbyte>(1024);\n"
        "        JMethodID readMethodID = JNI.GetMethodID(JNI.GetObjectClass(asset), \"read\", \"([B)I\");\n\n"
        "        int bytesRead = 0;\n"
        "        while ((bytesRead = JNI.CallMethod<int>(asset, readMethodID, new JValue(buffer))) > 0)\n"
        "        {\n"
        "            byte[] managedBuffer = (byte[])(Array)buffer.GetElements();\n"
        "            outputStream.Write(managedBuffer, 0, bytesRead);\n"
        "        }\n\n"
        "        using JClass assetClass = JNI.GetObjectClass(asset);\n"
        "        JMethodID closeMethodID = JNI.GetMethodID(assetClass, \"close\", \"()V\");\n"
        "        JNI.CallVoidMethod(asset, closeMethodID);",
        "    public static byte[] GetAssetBytes(string path)\n"
        "    {\n"
        "        if (assetManager == null || !assetManager.Valid())\n"
        "            return [];\n"
        "        using JString pathString = JNI.NewString(path);\n"
        "        using JClass assetManagerClass = JNI.GetObjectClass(assetManager);\n"
        "        using JObject asset = JNI.CallObjectMethod<JObject>(assetManager, JNI.GetMethodID(assetManagerClass, \"open\", \"(Ljava/lang/String;)Ljava/io/InputStream;\"), new JValue(pathString));\n"
        "        HandleException();\n"
        "        if (asset == null || !asset.Valid())\n"
        "            return [];\n\n"
        "        using MemoryStream outputStream = new();\n"
        "        using JArray<sbyte> buffer = JNI.NewArray<sbyte>(65536);\n"
        "        using JClass assetClass = JNI.GetObjectClass(asset);\n"
        "        JMethodID readMethodID = JNI.GetMethodID(assetClass, \"read\", \"([B)I\");\n\n"
        "        int bytesRead = 0;\n"
        "        while ((bytesRead = JNI.CallMethod<int>(asset, readMethodID, new JValue(buffer))) > 0)\n"
        "        {\n"
        "            byte[] managedBuffer = (byte[])(Array)buffer.GetElements();\n"
        "            outputStream.Write(managedBuffer, 0, bytesRead);\n"
        "        }\n\n"
        "        JMethodID closeMethodID = JNI.GetMethodID(assetClass, \"close\", \"()V\");\n"
        "        JNI.CallVoidMethod(asset, closeMethodID);",
        "APKAssetManager.cs GetAssetBytes HandleException + JClass reuse + 64KB buffer",
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
        "        var list = new System.Collections.Generic.List<string>(assets.Length);\n"
        "        foreach (JString js in assets)\n"
        "        {\n"
        "            using (js)\n"
        "                list.Add(js.GetString());\n"
        "        }\n"
        "        return [.. list];\n"
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
        "        bool found = false;\n"
        "        foreach (JString js in assets)\n"
        "        {\n"
        "            using (js)\n"
        "            {\n"
        "                if (!found && string.Equals(JNI.GetJStringString(js), fileName, StringComparison.Ordinal))\n"
        "                    found = true;\n"
        "            }\n"
        "        }\n"
        "        return found;\n"
        "    }",
        "APKAssetManager.cs GetDirectoryContents + DoesAssetExist safe root/null + dispose JString",
    )
    aam_text = replace_once(
        aam_text,
        "        public override int Read(byte[] buffer, int offset, int count)\n"
        "        {\n"
        "            using JArray<sbyte> javaBuffer = JNI.NewArray<sbyte>(count);\n\n"
        "            int read = JNI.CallMethod<int>(_streamObject, _readJmid, new JValue(javaBuffer), new JValue(offset), new JValue(count));\n"
        "            HandleException();\n\n"
        "            if (read == -1)\n"
        "                return 0;\n\n"
        "            for (int i = 0; i < read; i++)\n"
        "            {\n"
        "                buffer[i] = (byte)javaBuffer[i];\n"
        "            }\n\n"
        "            _pos += read;\n"
        "            return read;\n"
        "        }",
        "        public override int Read(byte[] buffer, int offset, int count)\n"
        "        {\n"
        "            if (count <= 0)\n"
        "                return 0;\n"
        "            using JArray<sbyte> javaBuffer = JNI.NewArray<sbyte>(count);\n\n"
        "            int read = JNI.CallMethod<int>(_streamObject, _readJmid, new JValue(javaBuffer), new JValue(0), new JValue(count));\n"
        "            HandleException();\n\n"
        "            if (read <= 0)\n"
        "                return 0;\n\n"
        "            sbyte[] elems = javaBuffer.GetElements();\n"
        "            Buffer.BlockCopy(elems, 0, buffer, offset, read);\n\n"
        "            _pos += read;\n"
        "            return read;\n"
        "        }",
        "APKAssetManager.cs APKAssetStream.Read offset + BlockCopy fix",
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

static const char *base_name(const char *p) {
    if (!p) return "<unknown>";
    const char *s = strrchr(p, '/');
    return s ? (s + 1) : p;
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
        mod_pc = base_name(dl_pc.dli_fname);
        off_pc = (uintptr_t)pc - (uintptr_t)dl_pc.dli_fbase;
    }

    Dl_info dl_lr;
    memset(&dl_lr, 0, sizeof(dl_lr));
    const char *mod_lr = "<unknown>";
    uintptr_t off_lr = 0;
    if (lr && dladdr((void *)(uintptr_t)lr, &dl_lr) && dl_lr.dli_fname) {
        mod_lr = base_name(dl_lr.dli_fname);
        off_lr = (uintptr_t)lr - (uintptr_t)dl_lr.dli_fbase;
    }

    char buf[1024];
    snprintf(
        buf,
        sizeof(buf),
        "[ML073] %s(%d) code=%d fault=0x%lx pc=%s+0x%lx lr=%s+0x%lx last=[%s]",
        sig_name(sig),
        sig,
        code,
        (unsigned long)fault,
        mod_pc,
        (unsigned long)off_pc,
        mod_lr,
        (unsigned long)off_lr,
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

void lemon_clear_icache(void *start, void *end) {
    if (start && end && end > start) {
        __builtin___clear_cache((char *)start, (char *)end);
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
        lemon_clear_icache;
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

    private static int _corehostCheckpointCount;

    public static void RedirectStdErr()
    {
        RedirectStream(STDERR_FILENO, "MelonLoader");
    }

    public static void RedirectStdOut()
    {
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
                        if ((logMsg.Contains("error", StringComparison.OrdinalIgnoreCase) ||
                            logMsg.Contains("exception", StringComparison.OrdinalIgnoreCase) ||
                            logMsg.Contains("fail", StringComparison.OrdinalIgnoreCase)) &&
                            Interlocked.Increment(ref _corehostCheckpointCount) <= 20)
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
        Environment.SetEnvironmentVariable("DOTNET_EnableWriteXorExecute", "0");
        Environment.SetEnvironmentVariable("SSL_CERT_DIR", "/system/etc/security/cacerts");
        Environment.SetEnvironmentVariable("SSL_CERT_FILE", "/system/etc/security/cacerts");
        EarlyLog.Checkpoint("bootstrap", $"Env set DOTNET_ROOT={DotnetDir} W^X=0 SSL_CERT_DIR=/system/etc/security/cacerts");

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
        var melonMarker = Path.Combine(melonloaderFolder, ".lemon_copied");
        var melonSentinel = Path.Combine(melonloaderFolder, "net6", "MelonLoader.dll");
        var baseInternalFolder = $"/data/data/{PackageName}/";
        var dotnetFolder = $"/data/data/{PackageName}/dotnet";
        var dotnetMarker = Path.Combine(dotnetFolder, ".lemon_copied");
        var dotnetSentinel = Path.Combine(dotnetFolder, "shared", "Microsoft.NETCore.App", "8.0.6", "libcoreclr.so");
        string stamp = date == default ? "0" : date.UtcDateTime.ToString("O");

        try
        {
            Directory.CreateDirectory(baseDataDir);
            bool melonUpToDate = Directory.Exists(melonloaderFolder)
                && File.Exists(melonSentinel)
                && File.Exists(melonMarker)
                && File.ReadAllText(melonMarker).Trim() == stamp
                && Directory.GetLastWriteTimeUtc(melonloaderFolder) > date;

            if (!melonUpToDate)
            {
                EarlyLog.Checkpoint("assets_copy", $"Copying assets/MelonLoader -> {baseDataDir}");
                APKAssetManager.SaveItemToDirectory("MelonLoader", baseDataDir, true);
                File.WriteAllText(melonMarker, stamp);
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
            bool dotnetUpToDate = Directory.Exists(dotnetFolder)
                && File.Exists(dotnetSentinel)
                && File.Exists(dotnetMarker)
                && File.ReadAllText(dotnetMarker).Trim() == stamp
                && Directory.GetLastWriteTimeUtc(dotnetFolder) > date;

            if (!dotnetUpToDate)
            {
                EarlyLog.Checkpoint("assets_copy", $"Copying assets/dotnet -> {baseInternalFolder}");
                APKAssetManager.SaveItemToDirectory("dotnet", baseInternalFolder, true);
                File.WriteAllText(dotnetMarker, stamp);
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
        "    }\n\n"
        "    private static nint FindLibUnityAddress()\n"
        "    {\n"
        "        if (Proxy.Android.AndroidProxy.UnityHandle != IntPtr.Zero)\n"
        "        {\n"
        "            foreach (string sym in new[] { \"JNI_OnLoad\", \"UnityMain\", \"UnitySendMessage\", \"JNI_OnUnload\", \"nativeDone\" })\n"
        "            {\n"
        "                if (NativeLibrary.TryGetExport(Proxy.Android.AndroidProxy.UnityHandle, sym, out nint addr) && addr != IntPtr.Zero)\n"
        "                    return addr;\n"
        "            }\n"
        "        }\n"
        "        try\n"
        "        {\n"
        "            foreach (string line in File.ReadLines(\"/proc/self/maps\"))\n"
        "            {\n"
        "                if (!line.Contains(\"libunity.so\", StringComparison.Ordinal)) continue;\n"
        "                int dash = line.IndexOf('-');\n"
        "                if (dash > 0 && ulong.TryParse(line[..dash], System.Globalization.NumberStyles.HexNumber, null, out ulong start))\n"
        "                    return (nint)start;\n"
        "            }\n"
        "        }\n"
        "        catch { }\n"
        "        return IntPtr.Zero;\n"
        "    }\n"
        "#endif",
        "PltHook.cs Android PlayerFileName + FindLibUnityAddress",
    )
    plt_text = replace_once(
        plt_text,
        "#else\n            pltHookOpened = PlthookOpen(ref pltHook, PlayerFileName) == 0;\n#endif",
        "#elif ANDROID\n"
        "        nint unityAddr = FindLibUnityAddress();\n"
        "        if (unityAddr != IntPtr.Zero)\n"
        "            pltHookOpened = PlthookOpenByAddress(ref pltHook, unityAddr) == 0;\n"
        "        if (!pltHookOpened && Proxy.Android.AndroidProxy.UnityHandle != IntPtr.Zero)\n"
        "            pltHookOpened = PlthookOpenByHandle(ref pltHook, Proxy.Android.AndroidProxy.UnityHandle) == 0;\n"
        "        if (!pltHookOpened && !string.IsNullOrEmpty(PlayerFileName))\n"
        "            pltHookOpened = PlthookOpen(ref pltHook, PlayerFileName) == 0;\n"
        "        Proxy.Android.EarlyLog.Checkpoint(\"plthook\", $\"pltHookOpened={pltHookOpened} addr=0x{unityAddr:X} target={PlayerFileName ?? \"<handle>\"}\");\n"
        "#else\n"
        "            pltHookOpened = PlthookOpen(ref pltHook, PlayerFileName) == 0;\n"
        "#endif",
        "PltHook.cs Android open by address first",
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
        "        internal static nint GetSymbol(nint handle, string symbolName)\n"
        "            => GetSymbol(handle, Marshal.StringToHGlobalAnsi(symbolName));",
        "        internal static nint GetSymbol(nint handle, string symbolName)\n"
        "        {\n"
        "            nint symPtr = Marshal.StringToHGlobalAnsi(symbolName);\n"
        "            try { return GetSymbol(handle, symPtr); }\n"
        "            finally { Marshal.FreeHGlobal(symPtr); }\n"
        "        }",
        "ModuleSymbolRedirect.cs GetSymbol FreeHGlobal",
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
        "            if (originalSymbolAddress == IntPtr.Zero)\n"
        "                return IntPtr.Zero;\n\n"
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

    # 6b. Utils/WineUtils.cs
    wu_path = boot / "Utils/WineUtils.cs"
    wu_text = wu_path.read_text(encoding="utf-8")
    wu_text = replace_once(
        wu_text,
        "#if LINUX\n        IsWine = false;\n#endif",
        "#if LINUX || ANDROID\n        IsWine = false;\n#endif",
        "WineUtils.cs ANDROID",
    )
    write_utf8(wu_path, wu_text)

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
        "    private static void LogToFiles(string? log)\n"
        "    {\n"
        "        foreach (var file in logFiles)\n"
        "        {\n"
        "            file.BaseStream.Seek(0, SeekOrigin.End);\n"
        "            if (log == null)\n"
        "            {\n"
        "                file.WriteLine();\n"
        "                continue;\n"
        "            }\n\n"
        "            file.WriteLine(log);\n"
        "        }\n"
        "    }",
        "    private static void LogToFiles(string? log)\n"
        "    {\n"
        "        lock (logFiles)\n"
        "        {\n"
        "            foreach (var file in logFiles)\n"
        "            {\n"
        "                file.BaseStream.Seek(0, SeekOrigin.End);\n"
        "                if (log == null)\n"
        "                {\n"
        "                    file.WriteLine();\n"
        "                    continue;\n"
        "                }\n\n"
        "                file.WriteLine(log);\n"
        "            }\n"
        "        }\n"
        "    }",
        "MelonLogger.cs lock LogToFiles",
    )
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
    private static readonly object _invokeLock = new();

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

        lock (_invokeLock)
        {
            if (invokeStarted)
                return result;
            invokeStarted = true;
        }
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

    # 1. MelonLoader.csproj: support AndroidBuild (net8.0 + JNI + APKAssetManager + LemonLoader/{MonoMod,HarmonyX,Il2CppInterop})
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
        "        <PackageReference Include=\"HarmonyX\" Version=\"$(HarmonyXVersion)\" />\n"
        "        <PackageReference Include=\"Mono.Cecil\" Version=\"$(MonoCecilVersion)\" />\n"
        "        <PackageReference Include=\"MonoMod\" Version=\"$(MonoModVersion)\" />\n"
        "        <PackageReference Include=\"MonoMod.RuntimeDetour\" Version=\"$(MonoModVersion)\" />\n"
        "        <PackageReference Include=\"MonoMod.Utils\" Version=\"$(MonoModVersion)\" />",
        "        <PackageReference Include=\"HarmonyX\" Version=\"$(HarmonyXVersion)\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Mono.Cecil\" Version=\"$(MonoCecilVersion)\" />\n"
        "        <PackageReference Include=\"MonoMod\" Version=\"$(MonoModVersion)\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"MonoMod.RuntimeDetour\" Version=\"$(MonoModVersion)\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"MonoMod.Utils\" Version=\"$(MonoModVersion)\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <ProjectReference Include=\"..\\External\\HarmonyX\\Harmony\\Harmony.csproj\" Condition=\"'$(AndroidBuild)' == 'true'\" />",
        "MelonLoader.csproj swap HarmonyX/MonoMod to LemonLoader forks on Android",
    )
    csproj_text = replace_once(
        csproj_text,
        "        <PackageReference Include=\"Iced\" Version=\"$(IcedVersion)\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Common\" Version=\"$(Il2CppInteropVersion)\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Runtime\" Version=\"$(Il2CppInteropVersion)\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Generator\" Version=\"$(Il2CppInteropVersion)\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.HarmonySupport\" Version=\"$(Il2CppInteropVersion)\" />",
        "        <PackageReference Include=\"Iced\" Version=\"$(IcedVersion)\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Common\" Version=\"$(Il2CppInteropVersion)\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Runtime\" Version=\"$(Il2CppInteropVersion)\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Generator\" Version=\"$(Il2CppInteropVersion)\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.HarmonySupport\" Version=\"$(Il2CppInteropVersion)\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "    </ItemGroup>\n"
        "    <ItemGroup Condition=\"'$(AndroidBuild)' == 'true'\">\n"
        "        <ProjectReference Include=\"..\\External\\Il2CppInterop\\Il2CppInterop.Common\\Il2CppInterop.Common.csproj\" />\n"
        "        <ProjectReference Include=\"..\\External\\Il2CppInterop\\Il2CppInterop.Runtime\\Il2CppInterop.Runtime.csproj\" />\n"
        "        <ProjectReference Include=\"..\\External\\Il2CppInterop\\Il2CppInterop.Generator\\Il2CppInterop.Generator.csproj\" />\n"
        "        <ProjectReference Include=\"..\\External\\Il2CppInterop\\Il2CppInterop.HarmonySupport\\Il2CppInterop.HarmonySupport.csproj\" />\n"
        "        <Reference Include=\"MonoMod.Backports\">\n"
        "            <HintPath>..\\External\\MonoMod\\artifacts\\bin\\MonoMod.Backports\\$(Configuration.ToLower())_net8.0\\MonoMod.Backports.dll</HintPath>\n"
        "        </Reference>\n"
        "        <Reference Include=\"MonoMod.Core\">\n"
        "            <HintPath>..\\External\\MonoMod\\artifacts\\bin\\MonoMod.Core\\$(Configuration.ToLower())_net8.0\\MonoMod.Core.dll</HintPath>\n"
        "        </Reference>\n"
        "        <Reference Include=\"MonoMod.ILHelpers\">\n"
        "            <HintPath>..\\External\\MonoMod\\artifacts\\bin\\MonoMod.ILHelpers\\$(Configuration.ToLower())_net8.0\\MonoMod.ILHelpers.dll</HintPath>\n"
        "        </Reference>\n"
        "        <Reference Include=\"MonoMod.RuntimeDetour\">\n"
        "            <HintPath>..\\External\\MonoMod\\artifacts\\bin\\MonoMod.RuntimeDetour\\$(Configuration.ToLower())_net8.0\\MonoMod.RuntimeDetour.dll</HintPath>\n"
        "        </Reference>\n"
        "        <Reference Include=\"MonoMod.RuntimeDetour.HookGen\">\n"
        "            <HintPath>..\\External\\MonoMod\\artifacts\\bin\\MonoMod.RuntimeDetour.HookGen\\$(Configuration.ToLower())_net8.0\\MonoMod.RuntimeDetour.HookGen.dll</HintPath>\n"
        "        </Reference>\n"
        "        <Reference Include=\"MonoMod.Utils\">\n"
        "            <HintPath>..\\External\\MonoMod\\artifacts\\bin\\MonoMod.Utils\\$(Configuration.ToLower())_net8.0\\MonoMod.Utils.dll</HintPath>\n"
        "        </Reference>",
        "MelonLoader.csproj swap Il2CppInterop + MonoMod references on Android",
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
        "using MonoMod.RuntimeDetour;\nusing MonoMod.RuntimeDetour.Platforms;",
        "using MonoMod.RuntimeDetour;\n#if !ANDROID\nusing MonoMod.RuntimeDetour.Platforms;\n#endif",
        "MelonLoader/Core.cs guard MonoMod.RuntimeDetour.Platforms on Android",
    )
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

    # 3b. InternalUtils/BootstrapInterop.cs -> skip CoreClrDelegateFixer.SanityCheckDetour on Android (ClrMD/ptrace)
    bi_path = ml / "InternalUtils/BootstrapInterop.cs"
    bi_text = bi_path.read_text(encoding="utf-8")
    bi_text = replace_once(
        bi_text,
        "    public static void NativeHookAttach(nint target, nint detour)\n"
        "    {\n"
        "#if NET6_0_OR_GREATER\n"
        "        // SanityCheckDetour is able to wrap and fix the bad method in a delegate where possible, so we pass the detour by ref.\n"
        "        // Herp: Wine/Proton are missing the PssCaptureSnapshot export from kernel32.dll so we skip CoreClrDelegateFixer.SanityCheckDetour under that runtime\n"
        "        if (!MelonUtils.IsUnderWineOrSteamProton()\n"
        "            && !CoreClrDelegateFixer.SanityCheckDetour(ref detour))\n"
        "            return;\n"
        "#endif",
        "    public static void NativeHookAttach(nint target, nint detour)\n"
        "    {\n"
        "#if NET6_0_OR_GREATER && !ANDROID\n"
        "        // SanityCheckDetour is able to wrap and fix the bad method in a delegate where possible, so we pass the detour by ref.\n"
        "        // Herp: Wine/Proton are missing the PssCaptureSnapshot export from kernel32.dll so we skip CoreClrDelegateFixer.SanityCheckDetour under that runtime\n"
        "        if (!MelonUtils.IsUnderWineOrSteamProton()\n"
        "            && !CoreClrDelegateFixer.SanityCheckDetour(ref detour))\n"
        "            return;\n"
        "#endif",
        "BootstrapInterop.cs skip SanityCheckDetour on Android",
    )
    write_utf8(bi_path, bi_text)

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
    mu_text = replace_once(
        mu_text,
        "        public static unsafe bool IsGame32Bit() =>\n#if X64\n            false;\n#else\n            true;\n#endif",
        "        public static unsafe bool IsGame32Bit() =>\n#if X64 || ANDROID\n            false;\n#else\n            true;\n#endif",
        "MelonUtils.cs IsGame32Bit ANDROID is 64-bit",
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
        "        public static string GameExecutablePath { get; } =\n#if OSX\n            MelonUtils.GetPathAncestor(Process.GetCurrentProcess()!.MainModule!.FileName, 3);\n#elif ANDROID\n            Process.GetCurrentProcess().MainModule?.FileName ?? Path.Combine(MelonBaseDirectory, Path.GetFileName(MelonBaseDirectory));\n#else\n            Process.GetCurrentProcess().MainModule.FileName;\n#endif\n        public static string MelonLoaderDirectory { get; } = Path.Combine(MelonBaseDirectory, \"MelonLoader\");\n#if !ANDROID\n        public static string GameRootDirectory { get; } = Path.GetDirectoryName(GameExecutablePath);\n#else\n        public static string GameRootDirectory { get; } = MelonBaseDirectory;\n        public static string PackageName { get; } = ReadAndroidPackageName();\n        private static string ReadAndroidPackageName()\n        {\n            try\n            {\n                string cmdline = File.ReadAllText(\"/proc/self/cmdline\");\n                int nul = cmdline.IndexOf('\\0');\n                if (nul > 0) cmdline = cmdline[..nul];\n                cmdline = cmdline.Trim();\n                if (!string.IsNullOrEmpty(cmdline) && !cmdline.Contains('/'))\n                    return cmdline;\n            }\n            catch { }\n            return Path.GetFileName(MelonBaseDirectory);\n        }\n#endif",
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
        "            if (name is \"GameAssembly\" or \"GameAssembly.so\" or \"GameAssembly.dll\")\n"
        "                name = \"libil2cpp.so\";\n"
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
        "                    Stream bundleStream = FileOpenSeekableRead(bundlePath);\n"
        "                    BundleFileInstance bundleFile = assetsManager.LoadBundleFile(bundleStream, bundlePath);\n"
        "#endif\n"
        "                    instance = assetsManager.LoadAssetsFileFromBundle(bundleFile, \"globalgamemanagers\");\n"
        "                }\n"
        "                else\n"
        "                {\n"
        "#if !ANDROID\n"
        "                    instance = assetsManager.LoadAssetsFile(bundlePath, true);\n"
        "#else\n"
        "                    Stream bundleStream = FileOpenSeekableRead(bundlePath);\n"
        "                    instance = assetsManager.LoadAssetsFile(bundleStream, bundlePath, true);\n"
        "#endif\n"
        "                }",
        "UnityInformationHandler.cs APKAssetManager seekable bundle/assets loading",
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
        "#if !ANDROID\n            File.OpenRead(path);\n#else\n            APKAssetManager.GetAssetStream(path);\n#endif\n\n"
        "#if ANDROID\n"
        "        private static Stream FileOpenSeekableRead(string path)\n"
        "        {\n"
        "            using Stream? raw = APKAssetManager.GetAssetStream(path);\n"
        "            if (raw == null)\n"
        "                throw new FileNotFoundException($\"APK asset not found: {path}\", path);\n"
        "            var ms = new MemoryStream();\n"
        "            raw.CopyTo(ms);\n"
        "            ms.Position = 0;\n"
        "            return ms;\n"
        "        }\n"
        "#endif\n"
        "    }\n}",
        "UnityInformationHandler.cs FileExists/FileReadAllBytes/FileOpenRead/FileOpenSeekableRead helpers",
    )
    write_utf8(ui_path, ui_text)

    # 9. Fixes/Il2CppInterop/Il2CppICallInjector.cs -> libil2cpp.so + MonoMod 25.1.2 PlatformTriple on Android
    icall_path = ml / "Fixes/Il2CppInterop/Il2CppICallInjector.cs"
    icall_text = icall_path.read_text(encoding="utf-8")
    icall_text = replace_once(
        icall_text,
        "                string gameAssemblyName = \"GameAssembly\";",
        "#if ANDROID\n"
        "                string gameAssemblyName = \"libil2cpp.so\";\n"
        "#else\n"
        "                string gameAssemblyName = \"GameAssembly\";\n"
        "#endif",
        "Il2CppICallInjector.cs libil2cpp.so on Android",
    )
    icall_text = replace_once(
        icall_text,
        "            // Return the New Method\n"
        "            MethodInfo newMethod = trampoline.Generate().Pin();\n"
        "            return (patcher, trampoline, newMethod, newMethod.GetNativeStart());",
        "#if ANDROID\n"
        "            MethodInfo newMethod = trampoline.Generate();\n"
        "            var triple = global::MonoMod.Core.Platforms.PlatformTriple.Current;\n"
        "            using var pin = triple.PinMethodIfNeeded(newMethod);\n"
        "            return (patcher, trampoline, newMethod, triple.Runtime.GetMethodEntryPoint(newMethod));\n"
        "#else\n"
        "            // Return the New Method\n"
        "            MethodInfo newMethod = trampoline.Generate().Pin();\n"
        "            return (patcher, trampoline, newMethod, newMethod.GetNativeStart());\n"
        "#endif",
        "Il2CppICallInjector.cs MonoMod 25.1.2 PlatformTriple entrypoint on Android",
    )
    write_utf8(icall_path, icall_text)

    # 10. Fixes/Il2CppInterop/Il2CppInteropFixes.cs -> disable x86-only Iced DecoderForAddress/dead helpers on Android
    interop_fixes_path = ml / "Fixes/Il2CppInterop/Il2CppInteropFixes.cs"
    interop_fixes_text = interop_fixes_path.read_text(encoding="utf-8")
    interop_fixes_text = replace_once(
        interop_fixes_text,
        "using Iced.Intel;\nusing Il2CppInterop.Common.XrefScans;\nusing FlowControl = Iced.Intel.FlowControl;",
        "using Il2CppInterop.Common.XrefScans;\n#if !ANDROID\nusing Iced.Intel;\nusing FlowControl = Iced.Intel.FlowControl;\n#endif",
        "Il2CppInteropFixes.cs guard Iced.Intel on Android",
    )
    interop_fixes_text = replace_once(
        interop_fixes_text,
        "                DecoderForAddress = typeof(XrefScanner).GetMethod(\"DecoderForAddress\", BindingFlags.NonPublic | BindingFlags.Static);\n"
        "                ExtractTargetAddress = typeof(XrefScanner).GetMethod(\"ExtractTargetAddress\", BindingFlags.NonPublic | BindingFlags.Static);",
        "#if !ANDROID\n"
        "                DecoderForAddress = typeof(XrefScanner).GetMethod(\"DecoderForAddress\", BindingFlags.NonPublic | BindingFlags.Static);\n"
        "                ExtractTargetAddress = typeof(XrefScanner).GetMethod(\"ExtractTargetAddress\", BindingFlags.NonPublic | BindingFlags.Static);\n"
        "#endif",
        "Il2CppInteropFixes.cs guard DecoderForAddress/ExtractTargetAddress on Android",
    )
    interop_fixes_text = replace_once(
        interop_fixes_text,
        "        public static int EstimateFunctionSize(IntPtr functionStart, int maxSize = 1000)",
        "#if !ANDROID\n        public static int EstimateFunctionSize(IntPtr functionStart, int maxSize = 1000)",
        "Il2CppInteropFixes.cs guard unused x86 Iced helpers start",
    )
    interop_fixes_text = replace_once(
        interop_fixes_text,
        "                        yield return currentFunctionStart;\n"
        "                    }\n"
        "                }\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}",
        "                        yield return currentFunctionStart;\n"
        "                    }\n"
        "                }\n"
        "            }\n"
        "        }\n"
        "#endif\n"
        "    }\n"
        "}",
        "Il2CppInteropFixes.cs guard unused x86 Iced helpers end",
    )
    write_utf8(interop_fixes_path, interop_fixes_text)

    # 10b. MonoMod 25.1.2 compatibility in PatchShield, DetourContextDisposeFix, InstancePatchFix, and ForwardingAttributes
    ps_path = ml / "Attributes/PatchShield.cs"
    ps_text = ps_path.read_text(encoding="utf-8")
    ps_text = replace_once(
        ps_text,
        "\t\t\tHook.OnDetour += (detour, originalMethod, patchMethod, delegateTarget) => MethodCheck(originalMethod);\n"
        "\t\t\tILHook.OnDetour += (detour, originalMethod, ilmanipulator) => MethodCheck(originalMethod);\n"
        "\t\t\tDetour.OnDetour += (detour, originalMethod, patchMethod) => MethodCheck(originalMethod);",
        "#if ANDROID\n"
        "\t\t\tDetourManager.DetourApplied += (info) => MethodCheck(info.Method.Method);\n"
        "\t\t\tDetourManager.ILHookApplied += (info) => MethodCheck(info.Method.Method);\n"
        "#else\n"
        "\t\t\tHook.OnDetour += (detour, originalMethod, patchMethod, delegateTarget) => MethodCheck(originalMethod);\n"
        "\t\t\tILHook.OnDetour += (detour, originalMethod, ilmanipulator) => MethodCheck(originalMethod);\n"
        "\t\t\tDetour.OnDetour += (detour, originalMethod, patchMethod) => MethodCheck(originalMethod);\n"
        "#endif",
        "PatchShield.cs MonoMod 25.1.2 DetourManager events on Android",
    )
    write_utf8(ps_path, ps_text)

    dc_path = ml / "Fixes/MonoMod/DetourContextDisposeFix.cs"
    dc_text = dc_path.read_text(encoding="utf-8")
    dc_text = replace_once(
        dc_text,
        "        internal static void Install()\n        {\n            try",
        "        internal static void Install()\n        {\n#if ANDROID\n            return;\n#else\n            try",
        "DetourContextDisposeFix.cs skip on Android start",
    )
    dc_text = replace_once(
        dc_text,
        "            catch (Exception e)\n            {\n                MelonLogger.Warning(e);\n            }\n        }",
        "            catch (Exception e)\n            {\n                MelonLogger.Warning(e);\n            }\n#endif\n        }",
        "DetourContextDisposeFix.cs skip on Android end",
    )
    write_utf8(dc_path, dc_text)

    mm_ipf_path = ml / "Fixes/MonoMod/InstancePatchFix.cs"
    mm_ipf_text = mm_ipf_path.read_text(encoding="utf-8")
    mm_ipf_text = replace_once(
        mm_ipf_text,
        "            Hook.OnDetour += (detour, originalMethod, patchMethod, delegateTarget) => EnsureStatic(patchMethod);\n"
        "            Detour.OnDetour += (detour, originalMethod, patchMethod) => EnsureStatic(patchMethod);",
        "#if ANDROID\n"
        "            DetourManager.DetourApplied += (info) => EnsureStatic(info.Entry);\n"
        "#else\n"
        "            Hook.OnDetour += (detour, originalMethod, patchMethod, delegateTarget) => EnsureStatic(patchMethod);\n"
        "            Detour.OnDetour += (detour, originalMethod, patchMethod) => EnsureStatic(patchMethod);\n"
        "#endif",
        "Fixes/MonoMod/InstancePatchFix.cs DetourManager on Android",
    )
    write_utf8(mm_ipf_path, mm_ipf_text)

    hm_ipf_path = ml / "Fixes/Harmony/InstancePatchFix.cs"
    hm_ipf_text = hm_ipf_path.read_text(encoding="utf-8")
    hm_ipf_text = replace_once(
        hm_ipf_text,
        "            Hook.OnDetour += (detour, originalMethod, patchMethod, delegateTarget) => PatchMethod(patchMethod);\n"
        "            Detour.OnDetour += (detour, originalMethod, patchMethod) => PatchMethod(patchMethod);",
        "#if ANDROID\n"
        "            DetourManager.DetourApplied += (info) => PatchMethod(info.Entry);\n"
        "#else\n"
        "            Hook.OnDetour += (detour, originalMethod, patchMethod, delegateTarget) => PatchMethod(patchMethod);\n"
        "            Detour.OnDetour += (detour, originalMethod, patchMethod) => PatchMethod(patchMethod);\n"
        "#endif",
        "Fixes/Harmony/InstancePatchFix.cs DetourManager on Android",
    )
    write_utf8(hm_ipf_path, hm_ipf_text)

    fwd_rt_path = ml / "BackwardsCompatibility/ForwardingAttributes/MonoMod.RuntimeDetour.cs"
    write_utf8(
        fwd_rt_path,
        "#if ANDROID\n"
        "using System.Runtime.CompilerServices;\n\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.RuntimeDetour.DetourConfig))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.RuntimeDetour.DetourContext))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.RuntimeDetour.Hook))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.RuntimeDetour.ILHook))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.RuntimeDetour.HookGen.HookEndpointManager))]\n"
        "#else\n" + fwd_rt_path.read_text(encoding="utf-8") + "\n#endif\n",
    )

    fwd_hm_path = ml / "BackwardsCompatibility/ForwardingAttributes/0Harmony.cs"
    fwd_hm_text = fwd_hm_path.read_text(encoding="utf-8")
    fwd_hm_text = replace_once(
        fwd_hm_text,
        "[assembly: TypeForwardedTo(typeof(HarmonyLib.InlineSignature))]",
        "#if !ANDROID\n[assembly: TypeForwardedTo(typeof(HarmonyLib.InlineSignature))]\n#endif",
        "ForwardingAttributes/0Harmony.cs guard InlineSignature on Android",
    )
    write_utf8(fwd_hm_path, fwd_hm_text)

    fwd_ut_path = ml / "BackwardsCompatibility/ForwardingAttributes/MonoMod.Utils.cs"
    write_utf8(
        fwd_ut_path,
        "#if ANDROID\n"
        "using System.Runtime.CompilerServices;\n\n"
        "[assembly: TypeForwardedTo(typeof(IgnoresAccessChecksToAttribute))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.ModInterop.ModExportNameAttribute))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.ModInterop.ModImportNameAttribute))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.ModInterop.ModInteropManager))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.DynData<>))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.Extensions))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.FastReflectionHelper))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.GenericMethodInstantiationComparer))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.GenericTypeInstantiationComparer))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.LazyDisposable))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.LazyDisposable<>))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.DMDGenerator<>))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.DMDCecilGenerator))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.DMDEmitDynamicMethodGenerator))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.DynamicMethodDefinition))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.DynamicMethodReference))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.DynDll))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.Relinker))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.ICallSiteGenerator))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.MMReflectionImporter))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.ReflectionHelper))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.RelinkFailedException))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.RelinkTargetNotFoundException))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.WeakReferenceComparer))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.Cil.CecilILGenerator))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.Cil.ILGeneratorShim))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Utils.Cil.ILGeneratorShimExt))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Cil.ILContext))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Cil.MoveType))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Cil.SearchTarget))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Cil.ILCursor))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Cil.ILLabel))]\n"
        "[assembly: TypeForwardedTo(typeof(MonoMod.Cil.ILPatternMatchingExt))]\n"
        "#else\n" + fwd_ut_path.read_text(encoding="utf-8") + "\n#endif\n",
    )

    # 11. Dependencies/SupportModules/Il2Cpp/Il2Cpp.csproj + Main.cs
    sm_csproj_path = root / "Dependencies/SupportModules/Il2Cpp/Il2Cpp.csproj"
    sm_csproj_text = sm_csproj_path.read_text(encoding="utf-8")
    sm_csproj_text = replace_once(
        sm_csproj_text,
        "        <PackageReference Include=\"HarmonyX\" Version=\"$(HarmonyXVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "        <PackageReference Include=\"Iced\" Version=\"$(IcedVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Common\" Version=\"$(Il2CppInteropVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Runtime\" Version=\"$(Il2CppInteropVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.HarmonySupport\" Version=\"$(Il2CppInteropVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "        <PackageReference Include=\"Mono.Cecil\" Version=\"$(MonoCecilVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "        <PackageReference Include=\"MonoMod.RuntimeDetour\" Version=\"$(MonoModVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "        <PackageReference Include=\"MonoMod.Utils\" Version=\"$(MonoModVersion)\" ExcludeAssets=\"Runtime\" />",
        "        <PackageReference Include=\"HarmonyX\" Version=\"$(HarmonyXVersion)\" ExcludeAssets=\"Runtime\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Iced\" Version=\"$(IcedVersion)\" ExcludeAssets=\"Runtime\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Common\" Version=\"$(Il2CppInteropVersion)\" ExcludeAssets=\"Runtime\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Runtime\" Version=\"$(Il2CppInteropVersion)\" ExcludeAssets=\"Runtime\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.HarmonySupport\" Version=\"$(Il2CppInteropVersion)\" ExcludeAssets=\"Runtime\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Mono.Cecil\" Version=\"$(MonoCecilVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "        <PackageReference Include=\"MonoMod.RuntimeDetour\" Version=\"$(MonoModVersion)\" ExcludeAssets=\"Runtime\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"MonoMod.Utils\" Version=\"$(MonoModVersion)\" ExcludeAssets=\"Runtime\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "    </ItemGroup>\n"
        "    <ItemGroup Condition=\"'$(AndroidBuild)' == 'true'\">\n"
        "        <ProjectReference Include=\"$(SolutionDir)\\External\\HarmonyX\\Harmony\\Harmony.csproj\" Private=\"false\" />\n"
        "        <ProjectReference Include=\"$(SolutionDir)\\External\\Il2CppInterop\\Il2CppInterop.Common\\Il2CppInterop.Common.csproj\" Private=\"false\" />\n"
        "        <ProjectReference Include=\"$(SolutionDir)\\External\\Il2CppInterop\\Il2CppInterop.Runtime\\Il2CppInterop.Runtime.csproj\" Private=\"false\" />\n"
        "        <ProjectReference Include=\"$(SolutionDir)\\External\\Il2CppInterop\\Il2CppInterop.HarmonySupport\\Il2CppInterop.HarmonySupport.csproj\" Private=\"false\" />\n"
        "        <Reference Include=\"MonoMod.RuntimeDetour\">\n"
        "            <HintPath>$(SolutionDir)\\External\\MonoMod\\artifacts\\bin\\MonoMod.RuntimeDetour\\$(Configuration.ToLower())_net8.0\\MonoMod.RuntimeDetour.dll</HintPath>\n"
        "            <Private>false</Private>\n"
        "        </Reference>\n"
        "        <Reference Include=\"MonoMod.Utils\">\n"
        "            <HintPath>$(SolutionDir)\\External\\MonoMod\\artifacts\\bin\\MonoMod.Utils\\$(Configuration.ToLower())_net8.0\\MonoMod.Utils.dll</HintPath>\n"
        "            <Private>false</Private>\n"
        "        </Reference>",
        "SupportModules/Il2Cpp/Il2Cpp.csproj swap to LemonLoader forks on Android",
    )
    write_utf8(sm_csproj_path, sm_csproj_text)

    # UnityUtilities -> swap Il2CppInterop.Runtime to External/Il2CppInterop on Android
    for uu_rel in [
        "UnityUtilities/UnityEngine.Il2CppAssetBundleManager/UnityEngine.Il2CppAssetBundleManager.csproj",
        "UnityUtilities/UnityEngine.Il2CppImageConversionManager/UnityEngine.Il2CppImageConversionManager.csproj",
    ]:
        uu_path = root / uu_rel
        uu_text = uu_path.read_text(encoding="utf-8")
        uu_text = re.sub(
            r'<PackageReference Include="Il2CppInterop\.Runtime" Version="\$\(Il2CppInteropVersion\)" Private="false"(\s*/>|>.*?</PackageReference>)',
            lambda _m: (
                '<PackageReference Include="Il2CppInterop.Runtime" Version="$(Il2CppInteropVersion)" Private="false" Condition="\'$(AndroidBuild)\' != \'true\'" />\n'
                '        <ProjectReference Include="$(SolutionDir)\\External\\Il2CppInterop\\Il2CppInterop.Runtime\\Il2CppInterop.Runtime.csproj" Private="false" Condition="\'$(AndroidBuild)\' == \'true\'" />'
            ),
            uu_text,
            flags=re.DOTALL,
        )
        write_utf8(uu_path, uu_text)

    # 12. Dependencies/SupportModules/Il2Cpp/Main.cs -> DllImportResolver for GameAssembly -> libil2cpp.so on Android
    sm_main_path = root / "Dependencies/SupportModules/Il2Cpp/Main.cs"
    sm_main_text = sm_main_path.read_text(encoding="utf-8")
    sm_main_text = replace_once(
        sm_main_text,
        "            if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))\n"
        "            {\n"
        "                System.Runtime.InteropServices.NativeLibrary.SetDllImportResolver(typeof(Il2CppInteropRuntime).Assembly,\n"
        "                    MacOsIl2CppInteropLibraryResolver);\n"
        "            }",
        "            if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))\n"
        "            {\n"
        "                System.Runtime.InteropServices.NativeLibrary.SetDllImportResolver(typeof(Il2CppInteropRuntime).Assembly,\n"
        "                    MacOsIl2CppInteropLibraryResolver);\n"
        "            }\n"
        "#if ANDROID\n"
        "            System.Runtime.InteropServices.NativeLibrary.SetDllImportResolver(typeof(Il2CppInteropRuntime).Assembly,\n"
        "                AndroidIl2CppInteropLibraryResolver);\n"
        "#endif",
        "SupportModules/Il2Cpp/Main.cs register AndroidIl2CppInteropLibraryResolver",
    )
    sm_main_text = replace_once(
        sm_main_text,
        "        private static IntPtr MacOsIl2CppInteropLibraryResolver(string libraryName, Assembly assembly, DllImportSearchPath? searchPath)\n"
        "        {\n"
        "            if (libraryName == \"GameAssembly\")\n"
        "            {\n"
        "                string gameAssemblyPath = Path.Combine(MelonEnvironment.GameExecutablePath, \"Contents\", \"Frameworks\", $\"{libraryName}.dylib\");\n"
        "                return System.Runtime.InteropServices.NativeLibrary.Load(gameAssemblyPath);\n"
        "            }\n"
        "            return IntPtr.Zero;\n"
        "        }",
        "        private static IntPtr MacOsIl2CppInteropLibraryResolver(string libraryName, Assembly assembly, DllImportSearchPath? searchPath)\n"
        "        {\n"
        "            if (libraryName == \"GameAssembly\")\n"
        "            {\n"
        "                string gameAssemblyPath = Path.Combine(MelonEnvironment.GameExecutablePath, \"Contents\", \"Frameworks\", $\"{libraryName}.dylib\");\n"
        "                return System.Runtime.InteropServices.NativeLibrary.Load(gameAssemblyPath);\n"
        "            }\n"
        "            return IntPtr.Zero;\n"
        "        }\n\n"
        "#if ANDROID\n"
        "        private static IntPtr AndroidIl2CppInteropLibraryResolver(string libraryName, Assembly assembly, DllImportSearchPath? searchPath)\n"
        "        {\n"
        "            if (libraryName is \"GameAssembly\" or \"GameAssembly.so\" or \"GameAssembly.dll\")\n"
        "            {\n"
        "                if (System.Runtime.InteropServices.NativeLibrary.TryLoad(\"libil2cpp.so\", out IntPtr handle))\n"
        "                    return handle;\n"
        "                return NativeLibrary.LoadLib(\"libil2cpp.so\");\n"
        "            }\n"
        "            return IntPtr.Zero;\n"
        "        }\n"
        "#endif",
        "SupportModules/Il2Cpp/Main.cs AndroidIl2CppInteropLibraryResolver",
    )
    write_utf8(sm_main_path, sm_main_text)

    print("[patched] MelonLoader managed core + Il2Cpp SupportModule (GameAssembly->libil2cpp.so, PackageName, 64-bit, ClrMD guard)")


def patch_il2cpp_assembly_generator(root: Path) -> None:
    ag_dir = root / "Dependencies/Il2CppAssemblyGenerator"

    # Update Il2CppAssemblyGenerator.csproj to reference in-process Cpp2IL 2022.1.0-pre-release.21 when AndroidBuild=true
    ag_csproj = ag_dir / "Il2CppAssemblyGenerator.csproj"
    ag_csproj_text = ag_csproj.read_text(encoding="utf-8")
    ag_csproj_text = replace_once(
        ag_csproj_text,
        "        <PackageReference Include=\"Iced\" Version=\"$(IcedVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Generator\" Version=\"$(Il2CppInteropVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Common\" Version=\"$(Il2CppInteropVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Runtime\" Version=\"$(Il2CppInteropVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "        <PackageReference Include=\"Mono.Cecil\" Version=\"$(MonoCecilVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "    </ItemGroup>",
        "        <PackageReference Include=\"Iced\" Version=\"$(IcedVersion)\" ExcludeAssets=\"Runtime\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Generator\" Version=\"$(Il2CppInteropVersion)\" ExcludeAssets=\"Runtime\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Common\" Version=\"$(Il2CppInteropVersion)\" ExcludeAssets=\"Runtime\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Il2CppInterop.Runtime\" Version=\"$(Il2CppInteropVersion)\" ExcludeAssets=\"Runtime\" Condition=\"'$(AndroidBuild)' != 'true'\" />\n"
        "        <PackageReference Include=\"Mono.Cecil\" Version=\"$(MonoCecilVersion)\" ExcludeAssets=\"Runtime\" />\n"
        "    </ItemGroup>\n\n"
        "    <ItemGroup Condition=\"'$(AndroidBuild)' == 'true'\">\n"
        "        <ProjectReference Include=\"$(SolutionDir)\\External\\Cpp2IL\\Cpp2IL.Core\\Cpp2IL.Core.csproj\" />\n"
        "        <ProjectReference Include=\"$(SolutionDir)\\External\\Cpp2IL\\LibCpp2IL\\LibCpp2IL.csproj\" />\n"
        "        <ProjectReference Include=\"$(SolutionDir)\\External\\Il2CppInterop\\Il2CppInterop.Generator\\Il2CppInterop.Generator.csproj\" Private=\"false\" />\n"
        "        <ProjectReference Include=\"$(SolutionDir)\\External\\Il2CppInterop\\Il2CppInterop.Common\\Il2CppInterop.Common.csproj\" Private=\"false\" />\n"
        "        <ProjectReference Include=\"$(SolutionDir)\\External\\Il2CppInterop\\Il2CppInterop.Runtime\\Il2CppInterop.Runtime.csproj\" Private=\"false\" />\n"
        "    </ItemGroup>",
        "Il2CppAssemblyGenerator.csproj Android Cpp2IL + Il2CppInterop ProjectReferences",
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
        "                foreach (string line in File.ReadLines(\"/proc/self/maps\"))\n"
        "                {\n"
        "                    int slash = line.IndexOf('/');\n"
        "                    if (slash < 0) continue;\n"
        "                    string path = line[slash..].Trim();\n"
        "                    if (path.EndsWith(\"/libil2cpp.so\", StringComparison.Ordinal) && File.Exists(path))\n"
        "                        return path;\n"
        "                }\n"
        "            }\n"
        "            catch { }\n\n"
        "            try\n"
        "            {\n"
        "                static void ClearEx()\n"
        "                {\n"
        "                    if (JNI.ExceptionCheck())\n"
        "                        JNI.ExceptionClear();\n"
        "                }\n\n"
        "                JObject? ctxObj = null;\n"
        "                using (JClass unityClass = JNI.FindClass(\"com/unity3d/player/UnityPlayer\"))\n"
        "                {\n"
        "                    ClearEx();\n"
        "                    if (unityClass.Valid())\n"
        "                    {\n"
        "                        JFieldID fid = JNI.GetStaticFieldID(unityClass, \"currentActivity\", \"Landroid/app/Activity;\");\n"
        "                        ClearEx();\n"
        "                        if (fid.Handle != IntPtr.Zero)\n"
        "                        {\n"
        "                            ctxObj = JNI.GetStaticObjectField<JObject>(unityClass, fid);\n"
        "                            ClearEx();\n"
        "                        }\n"
        "                    }\n"
        "                }\n\n"
        "                if (ctxObj == null || !ctxObj.Valid())\n"
        "                {\n"
        "                    ctxObj?.Dispose();\n"
        "                    ctxObj = null;\n"
        "                    using JClass actThread = JNI.FindClass(\"android/app/ActivityThread\");\n"
        "                    ClearEx();\n"
        "                    if (actThread.Valid())\n"
        "                    {\n"
        "                        JMethodID mid = JNI.GetStaticMethodID(actThread, \"currentApplication\", \"()Landroid/app/Application;\");\n"
        "                        ClearEx();\n"
        "                        if (mid.Handle != IntPtr.Zero)\n"
        "                        {\n"
        "                            ctxObj = JNI.CallStaticObjectMethod<JObject>(actThread, mid);\n"
        "                            ClearEx();\n"
        "                        }\n"
        "                    }\n"
        "                }\n\n"
        "                if (ctxObj == null || !ctxObj.Valid())\n"
        "                {\n"
        "                    ctxObj?.Dispose();\n"
        "                    return \"\";\n"
        "                }\n\n"
        "                using (ctxObj)\n"
        "                using (JClass ctxCls = JNI.GetObjectClass(ctxObj))\n"
        "                {\n"
        "                    ClearEx();\n"
        "                    JMethodID getAppInfoMid = JNI.GetMethodID(ctxCls, \"getApplicationInfo\", \"()Landroid/content/pm/ApplicationInfo;\");\n"
        "                    ClearEx();\n"
        "                    using JObject applicationInfoObj = JNI.CallObjectMethod<JObject>(ctxObj, getAppInfoMid);\n"
        "                    ClearEx();\n"
        "                    if (applicationInfoObj == null || !applicationInfoObj.Valid())\n"
        "                        return \"\";\n\n"
        "                    using JClass appInfoCls = JNI.GetObjectClass(applicationInfoObj);\n"
        "                    ClearEx();\n"
        "                    JFieldID filesFieldId = JNI.GetFieldID(appInfoCls, \"nativeLibraryDir\", \"Ljava/lang/String;\");\n"
        "                    ClearEx();\n"
        "                    using JString pathJString = JNI.GetObjectField<JString>(applicationInfoObj, filesFieldId);\n"
        "                    ClearEx();\n"
        "                    if (pathJString == null || !pathJString.Valid())\n"
        "                        return \"\";\n\n"
        "                    string nativePath = pathJString.GetString();\n"
        "                    if (!string.IsNullOrEmpty(nativePath) && Directory.Exists(nativePath))\n"
        "                    {\n"
        "                        string candidate = Path.Combine(nativePath, \"libil2cpp.so\");\n"
        "                        if (File.Exists(candidate))\n"
        "                            return candidate;\n"
        "                        string[] directoryLibs = Directory.GetFiles(nativePath, \"*.so\");\n"
        "                        return directoryLibs.FirstOrDefault(s => s.EndsWith(\"libil2cpp.so\")) ?? \"\";\n"
        "                    }\n"
        "                    return \"\";\n"
        "                }\n"
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
            try
            {
                typeof(Cpp2IlApi).GetProperty("CurrentAppContext")?.SetValue(null, null);
                var libMain = typeof(LibCpp2IL.LibCpp2IlMain);
                libMain.GetField("TheMetadata")?.SetValue(null, null);
                libMain.GetField("Binary")?.SetValue(null, null);
                libMain.GetMethod("Reset")?.Invoke(null, null);
            }
            catch { }
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();

            Core.Logger.Msg($"Cpp2IL Done in {(DateTime.Now - executionStart).TotalMilliseconds:F0}ms");
            return true;
        }
    }
}
#endif
"""
    write_utf8(cpp2il_cs_path, android_cpp2il)

    # Patch Packages/Il2CppInterop.cs: disable Parallel on Android to reduce peak RSS on 4 GB devices and dispose input assemblies
    interop_cs_path = ag_dir / "Packages/Il2CppInterop.cs"
    if interop_cs_path.exists():
        interop_text = interop_cs_path.read_text(encoding="utf-8")
        interop_text = replace_once(
            interop_text,
            "                Parallel = true,",
            "#if ANDROID\n                Parallel = false,\n#else\n                Parallel = true,\n#endif",
            "Il2CppInterop.cs Parallel=false on Android",
        )
        interop_text = replace_once(
            interop_text,
            "            //inputAssemblies.ForEach(a => a.Dispose());",
            "#if ANDROID\n            try\n            {\n                resolver.Dispose();\n                inputAssemblies.Clear();\n                GC.Collect();\n                GC.WaitForPendingFinalizers();\n                GC.Collect();\n            }\n            catch { }\n#endif",
            "Il2CppInterop.cs Android cleanup + GC",
        )
        write_utf8(interop_cs_path, interop_text)

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


def patch_external_android_deps(root: Path) -> None:
    ext = root / "External"

    # 1. External/MonoMod: relax global.json rollForward + add mprotect/icache flush to AndroidSystem.PatchData
    mm_dir = ext / "MonoMod"
    if mm_dir.exists():
        mm_gj = mm_dir / "global.json"
        if mm_gj.exists():
            gj_text = mm_gj.read_text(encoding="utf-8")
            gj_text = gj_text.replace('"rollForward": "latestPatch"', '"rollForward": "latestFeature"')
            gj_text = gj_text.replace('"version": "8.0.301"', '"version": "8.0.0"')
            write_utf8(mm_gj, gj_text)

        iced_proj = mm_dir / "external/iced.csproj"
        if iced_proj.exists():
            iced_text = iced_proj.read_text(encoding="utf-8")
            if 'NRT.cs' not in iced_text:
                iced_text = replace_once(
                    iced_text,
                    "  <ItemGroup>\n    <None Remove=\"..\\..\\..\\..\\LICENSE.txt\" />",
                    "  <ItemGroup>\n    <Compile Remove=\"$(RealProjDir)NRT.cs\" />\n    <None Remove=\"..\\..\\..\\..\\LICENSE.txt\" />",
                    "MonoMod external/iced.csproj remove duplicate NRT.cs",
                )
                write_utf8(iced_proj, iced_text)

        android_sys_path = mm_dir / "src/MonoMod.Core/Platforms/Systems/AndroidSystem.cs"
        if android_sys_path.exists():
            as_text = android_sys_path.read_text(encoding="utf-8")
            if "lemon_clear_icache" not in as_text:
                as_text = replace_once(
                    as_text,
                    "        public unsafe void PatchData(PatchTargetKind patchKind, IntPtr patchTarget, ReadOnlySpan<byte> data, Span<byte> backup)\n"
                    "        {\n"
                    "            // TODO: should this be thread-safe? It definitely is not right now.\n"
                    "\n"
                    "            // no memory protection cause it seems to not work, idk, i have no idea how this works\n"
                    "\n"
                    "            var target = new Span<byte>((void*)patchTarget, data.Length);\n"
                    "            // now we copy target to backup, then data to target, then flush the instruction cache\n"
                    "            _ = target.TryCopyTo(backup);\n"
                    "            data.CopyTo(target);\n"
                    "        }",
                    "#if NET6_0_OR_GREATER\n"
                    "        private static readonly unsafe delegate* unmanaged[Cdecl]<IntPtr, IntPtr, void> s_clearIcache = ResolveClearIcache();\n"
                    "\n"
                    "        private static unsafe delegate* unmanaged[Cdecl]<IntPtr, IntPtr, void> ResolveClearIcache()\n"
                    "        {\n"
                    "            if (System.Runtime.InteropServices.NativeLibrary.TryLoad(\"libmain.so\", out var handle) &&\n"
                    "                System.Runtime.InteropServices.NativeLibrary.TryGetExport(handle, \"lemon_clear_icache\", out var fn))\n"
                    "            {\n"
                    "                return (delegate* unmanaged[Cdecl]<IntPtr, IntPtr, void>)fn;\n"
                    "            }\n"
                    "            return null;\n"
                    "        }\n"
                    "#endif\n\n"
                    "        public unsafe void PatchData(PatchTargetKind patchKind, IntPtr patchTarget, ReadOnlySpan<byte> data, Span<byte> backup)\n"
                    "        {\n"
                    "            var prot = patchKind == PatchTargetKind.Executable\n"
                    "                ? Unix.Protection.Read | Unix.Protection.Write | Unix.Protection.Execute\n"
                    "                : Unix.Protection.Read | Unix.Protection.Write;\n"
                    "            nint addr = patchTarget;\n"
                    "            nint size = data.Length;\n"
                    "            RoundToPageBoundary(ref addr, ref size);\n"
                    "            _ = Unix.Mprotect(addr, (nuint)size, prot);\n"
                    "\n"
                    "            var target = new Span<byte>((void*)patchTarget, data.Length);\n"
                    "            _ = target.TryCopyTo(backup);\n"
                    "            data.CopyTo(target);\n\n"
                    "#if NET6_0_OR_GREATER\n"
                    "            if (patchKind == PatchTargetKind.Executable && s_clearIcache != null)\n"
                    "            {\n"
                    "                s_clearIcache(patchTarget, patchTarget + data.Length);\n"
                    "            }\n"
                    "#endif\n"
                    "        }",
                    "AndroidSystem.cs Mprotect + lemon_clear_icache",
                )
                write_utf8(android_sys_path, as_text)

    # 2. External/HarmonyX: build netstandard2.0 cleanly on Linux without net35/net452 pack
    hx_csproj = ext / "HarmonyX/Harmony/Harmony.csproj"
    if hx_csproj.exists():
        hx_text = hx_csproj.read_text(encoding="utf-8")
        hx_text = replace_once(
            hx_text,
            "        <TargetFrameworks>net35;net452;netstandard2.0</TargetFrameworks>\n"
            "        <GeneratePackageOnBuild>true</GeneratePackageOnBuild>",
            "        <TargetFrameworks>netstandard2.0</TargetFrameworks>\n"
            "        <GeneratePackageOnBuild>false</GeneratePackageOnBuild>",
            "Harmony.csproj netstandard2.0 on Linux/Android",
        )
        write_utf8(hx_csproj, hx_text)

    # 3. External/Il2CppInterop: disable GeneratePackageOnBuild for faster project build
    il2_props = ext / "Il2CppInterop/Directory.Build.props"
    if il2_props.exists():
        il2_text = il2_props.read_text(encoding="utf-8")
        il2_text = il2_text.replace("<GeneratePackageOnBuild>true</GeneratePackageOnBuild>", "<GeneratePackageOnBuild>false</GeneratePackageOnBuild>")
        write_utf8(il2_props, il2_text)

    print("[patched] External/{MonoMod,HarmonyX,Il2CppInterop} Android build & mprotect/icache")


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
    patch_external_android_deps(root)
    patch_managed_melonloader(root)
    patch_il2cpp_assembly_generator(root)
    print("[success] LavaGang/MelonLoader v0.7.3 Open-Beta Android ARM64 port applied cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
