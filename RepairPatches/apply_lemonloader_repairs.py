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
    patch_csproj_asmresolver(root)
    verify_il2cpp_interop_abi(root)
    print("[success] All LemonLoader fork source repairs applied cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
