using AssetsTools.NET;
using MelonLoader.Installer.Core;
using MelonLoader.Installer.Core.Diagnostics;
using MelonLoader.Installer.Core.Repair;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using QuestPatcher.Axml;
using System.IO.Compression;
using System.Text;
using Xunit;

namespace MelonLoader.Installer.Tests;

public sealed class InstallerTests : IDisposable
{
    private readonly string _workDir;

    public InstallerTests()
    {
        _workDir = Path.Combine(Path.GetTempPath(), "lemon_tests_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_workDir);
    }

    public void Dispose()
    {
        try { Directory.Delete(_workDir, true); } catch { }
    }

    private sealed class ListLogger : IPatchLogger
    {
        public List<string> Lines { get; } = [];
        public void Clear() => Lines.Clear();
        public void Log(string message) => Lines.Add(message);
    }

    [Fact]
    public void ApkAnalyzer_DetectsProjectDreamUnity2022AndAndroid15Flags()
    {
        string apkPath = CreateSyntheticUnityApk(
            Path.Combine(_workDir, "sonic_dream_team.apk"),
            packageName: "com.sonicdreamteam.android",
            unityVersion: "2022.3.54f1",
            extractNativeLibs: false,
            targetSdk: 35);

        var report = ApkAnalyzer.Analyze(apkPath);

        Assert.Equal("com.sonicdreamteam.android", report.PackageName);
        Assert.Equal("2022.3.54f1", report.UnityVersionString);
        Assert.Equal(35, report.TargetSdkVersion);
        Assert.False(report.ExtractNativeLibs);
        Assert.True(report.HasArm64);
        Assert.True(report.HasLibUnity);
        Assert.True(report.HasLibIl2Cpp);
        Assert.True(report.IsSupportedUnityIl2CppArm64);
        Assert.True(report.LibUnityElf?.IsArm64);
        Assert.True(report.LibIl2CppElf?.IsArm64);
        Assert.Empty(report.Errors);
    }

    [Fact]
    public void Diagnostics_DetectsBroken065Bugs_AndPassesRepairedLayout()
    {
        byte[] runtimeInitBytes = CompileCSharpAssembly("Il2CppInterop.Runtime", """
            namespace Il2CppInterop.Runtime.Injection {
                public class RegisterTypeOptions {
                    public bool LogSuccess { get; init; } = true;
                }
            }
            """);

        byte[] runtimeGetSetBytes = CompileCSharpAssembly("Il2CppInterop.Runtime", """
            namespace Il2CppInterop.Runtime.Injection {
                public class RegisterTypeOptions {
                    public bool LogSuccess { get; set; } = true;
                }
            }
            """);

        byte[] il2cppMismatchedBytes = CompileCSharpAssembly("Il2Cpp", """
            namespace MelonLoader.Support {
                public class Main {
                    public static void Test() {
                        var _ = new Il2CppInterop.Runtime.Injection.RegisterTypeOptions { LogSuccess = true };
                    }
                }
            }
            """,
            MetadataReference.CreateFromImage(runtimeGetSetBytes));

        byte[] il2cppMatchingBytes = CompileCSharpAssembly("Il2Cpp", """
            namespace MelonLoader.Support {
                public class Main {
                    public static void Test() {
                        var _ = new Il2CppInterop.Runtime.Injection.RegisterTypeOptions { LogSuccess = true };
                    }
                }
            }
            """,
            MetadataReference.CreateFromImage(runtimeInitBytes));

        byte[] brokenMlBytes = CompileCSharpAssembly("MelonLoader", """
            using System.Reflection;
            using System.Runtime.Loader;
            namespace MelonLoader.Fixes {
                public static class DotnetAssemblyLoadContextFix {
                    public static MethodInfo? Bad() => typeof(AssemblyLoadContext).GetMethod("LoadFromPath", BindingFlags.NonPublic | BindingFlags.Static);
                }
                public static class Il2CppInteropFixes { }
            }
            """);

        byte[] repairedMlBytes = CompileCSharpAssembly("MelonLoader", """
            using System.Reflection;
            using System.Runtime.Loader;
            namespace MelonLoader.Fixes {
                public static class DotnetAssemblyLoadContextFix {
                    public static MethodInfo? Good() => typeof(AssemblyLoadContext).GetMethod("InternalLoad", BindingFlags.NonPublic | BindingFlags.Instance);
                }
                public static class Il2CppInteropFixes { }
            }
            """);

        string brokenZip = Path.Combine(_workDir, "broken_065.zip");
        BuildMelonDataArchive(brokenZip, brokenMlBytes, runtimeInitBytes, il2cppMismatchedBytes, includeSslFix: false);

        var brokenReport = LemonDataDiagnostics.Inspect(brokenZip);
        Assert.False(brokenReport.IsHealthy);
        Assert.True(brokenReport.HasNullMethodForMelonLoaderBug);
        Assert.True(brokenReport.HasRegisterTypeOptionsMismatch);

        string repairedZip = Path.Combine(_workDir, "repaired_bundle.zip");
        BuildMelonDataArchive(repairedZip, repairedMlBytes, runtimeInitBytes, il2cppMatchingBytes, includeSslFix: true);

        var repairedReport = LemonDataDiagnostics.Inspect(repairedZip);
        Assert.True(repairedReport.IsHealthy, repairedReport.FormatReport());
        Assert.False(repairedReport.HasNullMethodForMelonLoaderBug);
        Assert.False(repairedReport.HasRegisterTypeOptionsMismatch);

        string repairedOutZip = Path.Combine(_workDir, "user_repaired.zip");
        var logger = new ListLogger();
        var result = LemonLoaderRepairer.RepairMelonDataZip(brokenZip, repairedOutZip, repairedZip, logger);
        Assert.True(result.IsHealthy, result.FormatReport());
    }

    [Fact]
    public void PatcherAndSigner_ProduceValidAndroid15SignedApk()
    {
        byte[] runtimeInitBytes = CompileCSharpAssembly("Il2CppInterop.Runtime", """
            namespace Il2CppInterop.Runtime.Injection {
                public class RegisterTypeOptions {
                    public bool LogSuccess { get; init; } = true;
                }
            }
            """);
        byte[] il2cppMatchingBytes = CompileCSharpAssembly("Il2Cpp", """
            namespace MelonLoader.Support {
                public class Main {
                    public static void Test() {
                        var _ = new Il2CppInterop.Runtime.Injection.RegisterTypeOptions { LogSuccess = true };
                    }
                }
            }
            """,
            MetadataReference.CreateFromImage(runtimeInitBytes));
        byte[] repairedMlBytes = CompileCSharpAssembly("MelonLoader", """
            namespace MelonLoader.Fixes {
                public static class DotnetAssemblyLoadContextFix { }
                public static class Il2CppInteropFixes { }
            }
            """);

        string repairedZip = Path.Combine(_workDir, "repaired_melon.zip");
        BuildMelonDataArchive(repairedZip, repairedMlBytes, runtimeInitBytes, il2cppMatchingBytes, includeSslFix: true);

        string unityDepsZip = Path.Combine(_workDir, "unity_deps.zip");
        using (var fs = File.Create(unityDepsZip))
        using (var z = new ZipArchive(fs, ZipArchiveMode.Create))
        {
            var e = z.CreateEntry("Libs/arm64-v8a/libunity.so");
            using var s = e.Open();
            s.Write(CreateFakeArm64Elf());
        }

        string inputApk = CreateSyntheticUnityApk(
            Path.Combine(_workDir, "game.apk"),
            packageName: "com.sonicdreamteam.android",
            unityVersion: "2022.3.54f1",
            extractNativeLibs: false,
            targetSdk: 35);

        string outDir = Path.Combine(_workDir, "out");
        string tempDir = Path.Combine(_workDir, "temp");
        Directory.CreateDirectory(outDir);
        Directory.CreateDirectory(tempDir);

        var logger = new ListLogger();
        var args = new PatchArguments(
            inputApk,
            "",
            [],
            outDir,
            tempDir,
            repairedZip,
            unityDepsZip,
            null,
            "com.sonicdreamteam.android",
            false);
        var patcher = new Patcher(args, logger);
        Assert.True(patcher.Run(), string.Join('\n', logger.Lines));

        string patchedApk = patcher.Info.OutputBaseApkPath;
        Assert.True(File.Exists(patchedApk));

        var postAnalysis = ApkAnalyzer.Analyze(patchedApk);
        Assert.True(postAnalysis.ExtractNativeLibs, "extractNativeLibs must be true for Android 15 compatibility");
        Assert.True(postAnalysis.HasV2SignatureBlock, "APK must contain V2 signing block");
        Assert.True(postAnalysis.HasLibBootstrap);
        Assert.True(postAnalysis.HasLibMain);
        Assert.True(postAnalysis.HasLibDobby);
        Assert.True(postAnalysis.HasMelonLoaderAssets);
        Assert.True(postAnalysis.HasDotnetAssets);
        AssertAllStoredSoEntriesAre16KbAligned(patchedApk);

        var postDiag = LemonDataDiagnostics.Inspect(patchedApk);
        Assert.True(postDiag.IsHealthy, postDiag.FormatReport());
    }

    [Fact]
    public void ApkAlignerAndSigner_AlignsNativeLibsTo16Kb_StripsStaleV1_AndEmitsV2V3SigningBlock()
    {
        string apkPath = Path.Combine(_workDir, "unaligned_v1_stale.apk");
        byte[] arm64Elf = CreateFakeArm64Elf();
        byte[] manifestBytes = CreateBinaryManifestBytes("com.example.test", extractNativeLibs: true, targetSdk: 35);

        using (var fs = File.Create(apkPath))
        using (var zip = new ZipArchive(fs, ZipArchiveMode.Create))
        {
            void Add(string name, byte[] data, CompressionLevel level)
            {
                var e = zip.CreateEntry(name, level);
                using var s = e.Open();
                s.Write(data);
            }

            Add("AndroidManifest.xml", manifestBytes, CompressionLevel.Optimal);
            Add("META-INF/MANIFEST.MF", Encoding.UTF8.GetBytes("Manifest-Version: 1.0\r\n"), CompressionLevel.Optimal);
            Add("META-INF/CERT.SF", Encoding.UTF8.GetBytes("Signature-Version: 1.0\r\n"), CompressionLevel.Optimal);
            Add("META-INF/CERT.RSA", new byte[128], CompressionLevel.Optimal);
            Add("META-INF/services/com.example.TestService", Encoding.UTF8.GetBytes("com.example.Impl"), CompressionLevel.Optimal);
            Add("lib/arm64-v8a/libunity.so", arm64Elf, CompressionLevel.NoCompression);
            Add("lib/arm64-v8a/libmain.so", arm64Elf, CompressionLevel.NoCompression);
            Add("assets/data.txt", Encoding.UTF8.GetBytes("hello-world"), CompressionLevel.Optimal);
        }

        string signedOnce = Path.Combine(_workDir, "signed_once.apk");
        var logger = new ListLogger();
        Core.Repair.StandaloneApkSigner.SignApk(apkPath, signedOnce, logger);

        // Re-signing an already signed APK must cleanly strip the previous APK Signing Block and re-align
        string signedTwice = Path.Combine(_workDir, "signed_twice.apk");
        Core.Repair.StandaloneApkSigner.SignApk(signedOnce, signedTwice, logger);

        string? verifyOut = Environment.GetEnvironmentVariable("VERIFY_SIGNED_APK_OUT");
        if (!string.IsNullOrWhiteSpace(verifyOut))
            File.Copy(signedTwice, verifyOut, overwrite: true);

        foreach (string signedPath in new[] { signedOnce, signedTwice })
        {
            AssertAllStoredSoEntriesAre16KbAligned(signedPath);
            AssertApkSigningBlockHasV2AndV3(signedPath);

            using var fs = File.OpenRead(signedPath);
            using var zip = new ZipArchive(fs, ZipArchiveMode.Read);
            Assert.Null(zip.GetEntry("META-INF/MANIFEST.MF"));
            Assert.Null(zip.GetEntry("META-INF/CERT.SF"));
            Assert.Null(zip.GetEntry("META-INF/CERT.RSA"));
            Assert.NotNull(zip.GetEntry("META-INF/services/com.example.TestService"));
            Assert.NotNull(zip.GetEntry("lib/arm64-v8a/libunity.so"));
            Assert.NotNull(zip.GetEntry("lib/arm64-v8a/libmain.so"));
        }
    }

    [Fact]
    public void ElfHeaderInspector_ParsesDynamicDependenciesSonameAndExports()
    {
        byte[] elf = CreateArm64ElfWithDynamicAndSymbols(
            soname: "libBootstrap.so",
            needed: ["liblog.so", "libdobby.so", "libc.so"],
            exports: ["JNI_OnLoad", "startup"]);

        var result = ElfHeaderInspector.Inspect(elf);

        Assert.True(result.IsValidElf);
        Assert.True(result.IsArm64);
        Assert.Equal("libBootstrap.so", result.Soname);
        Assert.Equal(new[] { "liblog.so", "libdobby.so", "libc.so" }, result.NeededLibraries);
        Assert.Contains("JNI_OnLoad", result.ExportedSymbols);
        Assert.Contains("startup", result.ExportedSymbols);
    }

    [Fact]
    public void Patcher_PreservesOriginalLibUnityByDefault_AndWritesStageConfig()
    {
        byte[] runtimeInitBytes = CompileCSharpAssembly("Il2CppInterop.Runtime", """
            namespace Il2CppInterop.Runtime.Injection {
                public class RegisterTypeOptions {
                    public bool LogSuccess { get; init; } = true;
                }
            }
            """);
        byte[] il2cppMatchingBytes = CompileCSharpAssembly("Il2Cpp", """
            namespace MelonLoader.Support {
                public class Main {
                    public static void Test() {
                        var _ = new Il2CppInterop.Runtime.Injection.RegisterTypeOptions { LogSuccess = true };
                    }
                }
            }
            """,
            MetadataReference.CreateFromImage(runtimeInitBytes));
        byte[] repairedMlBytes = CompileCSharpAssembly("MelonLoader", """
            namespace MelonLoader.Fixes {
                public static class DotnetAssemblyLoadContextFix { }
                public static class Il2CppInteropFixes { }
            }
            """);

        string repairedZip = Path.Combine(_workDir, "repaired_stage_test.zip");
        BuildMelonDataArchive(repairedZip, repairedMlBytes, runtimeInitBytes, il2cppMatchingBytes, includeSslFix: true);

        string inputApk = CreateSyntheticUnityApk(
            Path.Combine(_workDir, "dream_original.apk"),
            packageName: "com.sdt.port",
            unityVersion: "2022.3.54f1",
            extractNativeLibs: false,
            targetSdk: 35,
            libUnityMarker: "ORIGINAL_PROJECT_DREAM_LIBUNITY");

        byte[] originalLibUnity;
        using (var fs = File.OpenRead(inputApk))
        using (var z = new ZipArchive(fs, ZipArchiveMode.Read))
        using (var ms = new MemoryStream())
        {
            z.GetEntry("lib/arm64-v8a/libunity.so")!.Open().CopyTo(ms);
            originalLibUnity = ms.ToArray();
        }

        foreach (var stage in new[]
        {
            LemonPatchStage.NoHooks,
            LemonPatchStage.MinimalBootstrap,
            LemonPatchStage.Il2CppInitOnly,
            LemonPatchStage.Full
        })
        {
            string outDir = Path.Combine(_workDir, "out_" + stage);
            string tempDir = Path.Combine(_workDir, "tmp_" + stage);
            Directory.CreateDirectory(outDir);
            Directory.CreateDirectory(tempDir);

            var logger = new ListLogger();
            var args = new PatchArguments(
                inputApk,
                "",
                [],
                outDir,
                tempDir,
                repairedZip,
                "",
                null,
                "com.sdt.port",
                false,
                replaceLibUnity: false,
                patchStage: stage);

            var patcher = new Patcher(args, logger);
            Assert.True(patcher.Run(), string.Join('\n', logger.Lines));

            using var outFs = File.OpenRead(patcher.Info.OutputBaseApkPath);
            using var outZip = new ZipArchive(outFs, ZipArchiveMode.Read);

            // Original libunity.so must remain byte-for-byte identical
            using var unityMs = new MemoryStream();
            outZip.GetEntry("lib/arm64-v8a/libunity.so")!.Open().CopyTo(unityMs);
            Assert.Equal(originalLibUnity, unityMs.ToArray());

            // Stage marker must match
            using var sr = new StreamReader(outZip.GetEntry("assets/lemon_stage.txt")!.Open(), Encoding.UTF8);
            Assert.Equal(stage.ToAssetToken(), sr.ReadToEnd().Trim());
        }
    }

    [Fact]
    public void SourceBuiltRepairedBundle_WhenProvided_PassesAllDiagnosticsAndPatchesApk()
    {
        string? repairedBundle = Environment.GetEnvironmentVariable("REPAIRED_MELON_DATA");
        if (string.IsNullOrWhiteSpace(repairedBundle) || !File.Exists(repairedBundle))
            return;

        var diag = LemonDataDiagnostics.Inspect(repairedBundle);
        Assert.True(diag.IsHealthy, diag.FormatReport());
        Assert.False(diag.HasNullMethodForMelonLoaderBug);
        Assert.False(diag.HasRegisterTypeOptionsMismatch);
        Assert.Contains(diag.Checks, c => c.Id == "BOOTSTRAP_EARLY_LOGGING" && c.Severity == Core.Diagnostics.DiagnosticSeverity.Pass);
        Assert.Contains(diag.Checks, c => c.Id == "MAIN_EARLY_LOGGING" && c.Severity == Core.Diagnostics.DiagnosticSeverity.Pass);

        string inputApk = CreateSyntheticUnityApk(
            Path.Combine(_workDir, "dream_team_unpatched.apk"),
            packageName: "com.sdt.port",
            unityVersion: "2022.3.54f1",
            extractNativeLibs: false,
            targetSdk: 35,
            libUnityMarker: "ORIGINAL_PROJECT_DREAM_LIBUNITY");

        string repairedApk = Path.Combine(_workDir, "dream_team_repaired.apk");
        var logger = new ListLogger();
        var apkDiag = LemonLoaderRepairer.RepairPatchedApk(inputApk, repairedApk, repairedBundle, null, logger);
        Assert.True(apkDiag.IsHealthy, apkDiag.FormatReport());

        var postApk = ApkAnalyzer.Analyze(repairedApk);
        Assert.Contains("libmain.so", postApk.Arm64Libraries.Keys);
        Assert.Contains("JNI_OnLoad", postApk.Arm64Libraries["libmain.so"].ExportedSymbols);

        if (postApk.Arm64Libraries.ContainsKey("libBootstrap.so"))
        {
            Assert.Contains("JNI_OnLoad", postApk.Arm64Libraries["libBootstrap.so"].ExportedSymbols);
            Assert.Contains("startup", postApk.Arm64Libraries["libBootstrap.so"].ExportedSymbols);
        }
        else
        {
            // v0.7.3 NativeAOT single-library layout: libmain.so exports JNI_OnLoad + BootstrapInterop exports
            Assert.DoesNotContain("libBootstrap.so", postApk.Arm64Libraries.Keys);
            Assert.DoesNotContain("libdobby.so", postApk.Arm64Libraries.Keys);
            Assert.Contains("GetJavaVM", postApk.Arm64Libraries["libmain.so"].ExportedSymbols);
            Assert.Contains("GetLoaderConfig", postApk.Arm64Libraries["libmain.so"].ExportedSymbols);
            Assert.DoesNotContain("libc++_shared.so", postApk.Arm64Libraries["libmain.so"].NeededLibraries, StringComparer.OrdinalIgnoreCase);
            Assert.Contains(diag.Checks, c => c.Id == "MAIN_STATIC_LIBCXX" && c.Severity == Core.Diagnostics.DiagnosticSeverity.Pass);
            Assert.Contains(diag.Checks, c => c.Id == "CPP2IL_CORE_DLL" && c.Severity == Core.Diagnostics.DiagnosticSeverity.Pass);
        }
    }

    [Fact]
    public void Diagnostics_PassesNativeAot073Layout_AndRemovesStale06XLibsOnPatch()
    {
        byte[] runtimeInitBytes = CompileCSharpAssembly("Il2CppInterop.Runtime", """
            namespace Il2CppInterop.Runtime.Injection {
                public class RegisterTypeOptions {
                    public bool LogSuccess { get; init; } = true;
                }
            }
            """);
        byte[] il2cppMatchingBytes = CompileCSharpAssembly("Il2Cpp", """
            namespace MelonLoader.Support {
                public class Main {
                    public static void Test() {
                        var _ = new Il2CppInterop.Runtime.Injection.RegisterTypeOptions { LogSuccess = true };
                    }
                }
            }
            """,
            MetadataReference.CreateFromImage(runtimeInitBytes));
        byte[] ml073Bytes = CompileCSharpAssembly("MelonLoader", """
            namespace MelonLoader.Fixes.Il2CppInterop {
                public static class Il2CppICallInjector { }
            }
            """);
        byte[] harmonyBytes = CompileCSharpAssembly("0Harmony", "namespace HarmonyLib { public static class PatchFunctions { public static void ReversePatch() {} } }");
        byte[] genericValidPe = CompileCSharpAssembly("GenericLib", "namespace Dummy { public class Placeholder {} }");
        byte[] libMainElf = CreateArm64ElfWithDynamicAndSymbols(
            soname: "libmain.so",
            needed: ["liblog.so", "libdl.so", "libc.so"],
            exports: ["JNI_OnLoad", "GetJavaVM", "GetLoaderConfig", "LogMsg"]);
        // Embed SSL_CERT_DIR and lemon_early_crash.log markers in spare bytes
        Array.Resize(ref libMainElf, Math.Max(libMainElf.Length, 1024));
        Encoding.ASCII.GetBytes("SSL_CERT_DIR").CopyTo(libMainElf, libMainElf.Length - 128);
        Encoding.ASCII.GetBytes("lemon_early_crash.log").CopyTo(libMainElf, libMainElf.Length - 64);

        string bundle073 = Path.Combine(_workDir, "melon_073_nativeaot.zip");
        using (var fs = File.Create(bundle073))
        using (var zip = new ZipArchive(fs, ZipArchiveMode.Create))
        {
            void WriteBytes(string path, byte[] data)
            {
                var e = zip.CreateEntry(path);
                using var s = e.Open();
                s.Write(data);
            }

            WriteBytes("MelonLoader/net6/MelonLoader.dll", ml073Bytes);
            WriteBytes("MelonLoader/net6/Il2CppInterop.Runtime.dll", runtimeInitBytes);
            WriteBytes("MelonLoader/net6/0Harmony.dll", harmonyBytes);
            WriteBytes("MelonLoader/net6/Il2CppInterop.HarmonySupport.dll", genericValidPe);
            WriteBytes("MelonLoader/net6/Il2CppInterop.Common.dll", genericValidPe);
            WriteBytes("MelonLoader/net6/Il2CppInterop.Generator.dll", genericValidPe);
            WriteBytes("MelonLoader/net6/MonoMod.RuntimeDetour.dll", genericValidPe);
            WriteBytes("MelonLoader/net6/MelonLoader.NativeHost.dll", genericValidPe);
            WriteBytes("MelonLoader/net6/MelonLoader.runtimeconfig.json", Encoding.UTF8.GetBytes("{}"));
            WriteBytes("MelonLoader/Dependencies/SupportModules/Il2Cpp.dll", il2cppMatchingBytes);
            WriteBytes("MelonLoader/Dependencies/Il2CppAssemblyGenerator/Il2CppAssemblyGenerator.dll", genericValidPe);
            WriteBytes("MelonLoader/Dependencies/Il2CppAssemblyGenerator/Cpp2IL.Core.dll", genericValidPe);
            WriteBytes("dotnet/host/fxr/8.0.6/libhostfxr.so", CreateFakeArm64Elf());
            WriteBytes("dotnet/shared/Microsoft.NETCore.App/8.0.6/libcoreclr.so", CreateFakeArm64Elf());
            WriteBytes("dotnet/shared/Microsoft.NETCore.App/8.0.6/System.Runtime.dll", genericValidPe);
            WriteBytes("native/libmain.so", libMainElf);
        }

        var bundleDiag = LemonDataDiagnostics.Inspect(bundle073);
        Assert.True(bundleDiag.IsHealthy, bundleDiag.FormatReport());
        Assert.Contains(bundleDiag.Checks, c => c.Id == "MAIN_JNI_ONLOAD" && c.Severity == Core.Diagnostics.DiagnosticSeverity.Pass);

        string inputApk = CreateSyntheticUnityApk(
            Path.Combine(_workDir, "game_073.apk"),
            packageName: "com.sdt.port",
            unityVersion: "2022.3.54f1",
            extractNativeLibs: false,
            targetSdk: 35);

        string patchedApk = Path.Combine(_workDir, "game_073_patched.apk");
        var apkDiag = LemonLoaderRepairer.RepairPatchedApk(inputApk, patchedApk, bundle073, null, new ListLogger());
        Assert.True(apkDiag.IsHealthy, apkDiag.FormatReport());
    }

    private static byte[] CompileCSharpAssembly(string assemblyName, string code, params MetadataReference[] extraRefs)
    {
        var refs = new List<MetadataReference>
        {
            MetadataReference.CreateFromFile(typeof(object).Assembly.Location),
            MetadataReference.CreateFromFile(typeof(Console).Assembly.Location),
            MetadataReference.CreateFromFile(typeof(System.Runtime.Loader.AssemblyLoadContext).Assembly.Location),
            MetadataReference.CreateFromFile(Path.Combine(Path.GetDirectoryName(typeof(object).Assembly.Location)!, "System.Runtime.dll")),
        };
        refs.AddRange(extraRefs);

        var tree = CSharpSyntaxTree.ParseText(code);
        var compilation = CSharpCompilation.Create(
            assemblyName,
            [tree],
            refs,
            new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary));

        using MemoryStream ms = new();
        var result = compilation.Emit(ms);
        Assert.True(result.Success, string.Join('\n', result.Diagnostics.Select(d => d.ToString())));
        return ms.ToArray();
    }

    private static void BuildMelonDataArchive(
        string outputZip,
        byte[] melonLoaderDll,
        byte[] il2cppInteropRuntimeDll,
        byte[] il2cppSupportDll,
        bool includeSslFix)
    {
        byte[] harmonyBytes = CompileCSharpAssembly("0Harmony", "namespace HarmonyLib { public static class PatchFunctions { public static void ReversePatch() {} } }");
        byte[] genericValidPe = CompileCSharpAssembly("GenericLib", "namespace Dummy { public class Placeholder {} }");
        byte[] arm64Elf = CreateFakeArm64Elf(includeSslFix ? "SSL_CERT_DIR" : null);

        using var fs = File.Create(outputZip);
        using var zip = new ZipArchive(fs, ZipArchiveMode.Create);

        void WriteBytes(string path, byte[] data)
        {
            var e = zip.CreateEntry(path);
            using var s = e.Open();
            s.Write(data);
        }

        WriteBytes("MelonLoader/net8/MelonLoader.dll", melonLoaderDll);
        WriteBytes("MelonLoader/net8/Il2CppInterop.Runtime.dll", il2cppInteropRuntimeDll);
        WriteBytes("MelonLoader/net8/0Harmony.dll", harmonyBytes);
        WriteBytes("MelonLoader/net8/Il2CppInterop.HarmonySupport.dll", genericValidPe);
        WriteBytes("MelonLoader/net8/Il2CppInterop.Common.dll", genericValidPe);
        WriteBytes("MelonLoader/net8/Il2CppInterop.Generator.dll", genericValidPe);
        WriteBytes("MelonLoader/net8/JNISharp.dll", genericValidPe);
        WriteBytes("MelonLoader/net8/MonoMod.Core.dll", genericValidPe);
        WriteBytes("MelonLoader/net8/MonoMod.RuntimeDetour.dll", genericValidPe);
        WriteBytes("MelonLoader/net8/MelonLoader.NativeHost.dll", genericValidPe);
        WriteBytes("MelonLoader/net8/MelonLoader.runtimeconfig.json", Encoding.UTF8.GetBytes("{}"));
        WriteBytes("MelonLoader/Dependencies/SupportModules/Il2Cpp.dll", il2cppSupportDll);
        WriteBytes("MelonLoader/Dependencies/Il2CppAssemblyGenerator/Il2CppAssemblyGenerator.dll", genericValidPe);

        WriteBytes("dotnet/host/fxr/8.0.6/libhostfxr.so", arm64Elf);
        WriteBytes("dotnet/shared/Microsoft.NETCore.App/8.0.6/libcoreclr.so", arm64Elf);
        WriteBytes("dotnet/shared/Microsoft.NETCore.App/8.0.6/System.Runtime.dll", genericValidPe);

        WriteBytes("native/libBootstrap.so", arm64Elf);
        WriteBytes("native/libmain.so", CreateFakeArm64Elf());
        WriteBytes("native/libdobby.so", CreateFakeArm64Elf());
    }

    private static byte[] CreateFakeArm64Elf(string? embeddedMarker = null)
    {
        byte[] buf = new byte[512];
        buf[0] = 0x7F;
        buf[1] = (byte)'E';
        buf[2] = (byte)'L';
        buf[3] = (byte)'F';
        buf[4] = 2; // ELFCLASS64
        buf[5] = 1; // ELFDATA2LSB
        buf[6] = 1; // EV_CURRENT
        BitConverter.GetBytes((ushort)3).CopyTo(buf, 16); // ET_DYN
        BitConverter.GetBytes((ushort)0xB7).CopyTo(buf, 18); // EM_AARCH64
        BitConverter.GetBytes((uint)1).CopyTo(buf, 20);
        BitConverter.GetBytes((ulong)64).CopyTo(buf, 32); // e_phoff
        BitConverter.GetBytes((ushort)64).CopyTo(buf, 52); // e_ehsize
        BitConverter.GetBytes((ushort)56).CopyTo(buf, 54); // e_phentsize
        BitConverter.GetBytes((ushort)1).CopyTo(buf, 56); // e_phnum

        // 1 PT_LOAD segment at offset 64 with p_align = 16384
        BitConverter.GetBytes((uint)1).CopyTo(buf, 64); // PT_LOAD
        BitConverter.GetBytes((ulong)16384).CopyTo(buf, 64 + 48);

        if (!string.IsNullOrEmpty(embeddedMarker))
        {
            Encoding.ASCII.GetBytes(embeddedMarker).CopyTo(buf, 256);
        }

        return buf;
    }

    private static byte[] CreateArm64ElfWithDynamicAndSymbols(
        string soname,
        string[] needed,
        string[] exports)
    {
        using MemoryStream strTab = new();
        strTab.WriteByte(0); // null string at offset 0

        uint AddString(string s)
        {
            uint offset = (uint)strTab.Position;
            byte[] bytes = Encoding.ASCII.GetBytes(s);
            strTab.Write(bytes, 0, bytes.Length);
            strTab.WriteByte(0);
            return offset;
        }

        uint sonameOff = AddString(soname);
        var neededOffs = needed.Select(AddString).ToArray();
        var exportOffs = exports.Select(AddString).ToArray();
        byte[] strBytes = strTab.ToArray();

        // Layout:
        // 0..64: Elf64_Ehdr
        // 64..120: 1x Elf64_Phdr (PT_LOAD)
        // 128..: .dynstr
        // align to 8: .dynamic (Elf64_Dyn: 16 bytes each)
        // align to 8: .dynsym (Elf64_Sym: 24 bytes each, index 0 = STN_UNDEF)
        // align to 8: 4x Elf64_Shdr (null, .dynstr, .dynamic, .dynsym)
        int strOff = 128;
        int dynOff = (strOff + strBytes.Length + 7) & ~7;
        int dynCount = 1 + neededOffs.Length + 1; // DT_SONAME + DT_NEEDED... + DT_NULL
        int dynSize = dynCount * 16;
        int symOff = (dynOff + dynSize + 7) & ~7;
        int symCount = 1 + exportOffs.Length;
        int symSize = symCount * 24;
        int shOff = (symOff + symSize + 7) & ~7;
        int shCount = 4;
        int totalSize = shOff + shCount * 64;

        byte[] buf = new byte[totalSize];
        buf[0] = 0x7F;
        buf[1] = (byte)'E';
        buf[2] = (byte)'L';
        buf[3] = (byte)'F';
        buf[4] = 2;
        buf[5] = 1;
        buf[6] = 1;
        BitConverter.GetBytes((ushort)3).CopyTo(buf, 16); // ET_DYN
        BitConverter.GetBytes((ushort)0xB7).CopyTo(buf, 18); // EM_AARCH64
        BitConverter.GetBytes((uint)1).CopyTo(buf, 20);
        BitConverter.GetBytes((ulong)64).CopyTo(buf, 32); // e_phoff
        BitConverter.GetBytes((ulong)shOff).CopyTo(buf, 40); // e_shoff
        BitConverter.GetBytes((ushort)64).CopyTo(buf, 52); // e_ehsize
        BitConverter.GetBytes((ushort)56).CopyTo(buf, 54); // e_phentsize
        BitConverter.GetBytes((ushort)1).CopyTo(buf, 56); // e_phnum
        BitConverter.GetBytes((ushort)64).CopyTo(buf, 58); // e_shentsize
        BitConverter.GetBytes((ushort)shCount).CopyTo(buf, 60); // e_shnum

        // PT_LOAD
        BitConverter.GetBytes((uint)1).CopyTo(buf, 64);
        BitConverter.GetBytes((ulong)16384).CopyTo(buf, 64 + 48);

        // .dynstr
        strBytes.CopyTo(buf, strOff);

        // .dynamic
        int d = dynOff;
        void WriteDyn(long tag, ulong val)
        {
            BitConverter.GetBytes(tag).CopyTo(buf, d);
            BitConverter.GetBytes(val).CopyTo(buf, d + 8);
            d += 16;
        }
        WriteDyn(14, sonameOff); // DT_SONAME
        foreach (uint noff in neededOffs)
            WriteDyn(1, noff); // DT_NEEDED
        WriteDyn(0, 0); // DT_NULL

        // .dynsym
        int sy = symOff + 24; // skip STN_UNDEF
        foreach (uint eoff in exportOffs)
        {
            BitConverter.GetBytes(eoff).CopyTo(buf, sy); // st_name
            buf[sy + 4] = (1 << 4) | 2; // STB_GLOBAL | STT_FUNC
            BitConverter.GetBytes((ushort)1).CopyTo(buf, sy + 6); // st_shndx != 0 (defined)
            sy += 24;
        }

        // Section headers: 0=NULL, 1=.dynstr (SHT_STRTAB=3), 2=.dynamic (SHT_DYNAMIC=6), 3=.dynsym (SHT_DYNSYM=11)
        void WriteSh(int idx, uint type, ulong offset, ulong size, uint link, ulong entsize)
        {
            int o = shOff + idx * 64;
            BitConverter.GetBytes(type).CopyTo(buf, o + 4);
            BitConverter.GetBytes(offset).CopyTo(buf, o + 24);
            BitConverter.GetBytes(size).CopyTo(buf, o + 32);
            BitConverter.GetBytes(link).CopyTo(buf, o + 40);
            BitConverter.GetBytes(entsize).CopyTo(buf, o + 56);
        }

        WriteSh(1, 3, (ulong)strOff, (ulong)strBytes.Length, 0, 0);
        WriteSh(2, 6, (ulong)dynOff, (ulong)dynSize, 1, 16);
        WriteSh(3, 11, (ulong)symOff, (ulong)symSize, 1, 24);

        return buf;
    }

    private static byte[] CreateBinaryManifestBytes(string packageName, bool extractNativeLibs, int targetSdk)
    {
        Uri androidNs = new("http://schemas.android.com/apk/res/android");

        var manifest = new AxmlElement("manifest");
        manifest.Attributes.Add(new("package", null, 0, packageName));
        manifest.Attributes.Add(new("versionName", androidNs, 16843292, "1.0.0"));
        manifest.Attributes.Add(new("versionCode", androidNs, 16843291, 1));

        var usesSdk = new AxmlElement("uses-sdk");
        usesSdk.Attributes.Add(new("minSdkVersion", androidNs, 16843276, 24));
        usesSdk.Attributes.Add(new("targetSdkVersion", androidNs, 16843712, targetSdk));
        manifest.Children.Add(usesSdk);

        var app = new AxmlElement("application");
        app.Attributes.Add(new("extractNativeLibs", androidNs, 16844010, extractNativeLibs));
        manifest.Children.Add(app);

        using MemoryStream manifestStream = new();
        AxmlSaver.SaveDocument(manifestStream, manifest);
        return manifestStream.ToArray();
    }

    private static string CreateSyntheticUnityApk(
        string apkPath,
        string packageName,
        string unityVersion,
        bool extractNativeLibs,
        int targetSdk,
        string? libUnityMarker = null)
    {
        byte[] manifestBytes = CreateBinaryManifestBytes(packageName, extractNativeLibs, targetSdk);

        byte[] ggmBytes = CreateMinimalAssetsFileBytes(unityVersion);
        byte[] arm64Elf = CreateFakeArm64Elf();
        byte[] libUnityElf = CreateFakeArm64Elf(libUnityMarker);

        using (var fs = File.Create(apkPath))
        using (var zip = new ZipArchive(fs, ZipArchiveMode.Create))
        {
            void Add(string name, byte[] bytes)
            {
                var e = zip.CreateEntry(name, CompressionLevel.Optimal);
                using var s = e.Open();
                s.Write(bytes);
            }

            Add("AndroidManifest.xml", manifestBytes);
            Add("assets/bin/Data/globalgamemanagers", ggmBytes);
            Add("lib/arm64-v8a/libunity.so", libUnityElf);
            Add("lib/arm64-v8a/libil2cpp.so", arm64Elf);
            Add("lib/arm64-v8a/libmain.so", arm64Elf);
        }

        return apkPath;
    }

    private static byte[] CreateMinimalAssetsFileBytes(string unityVersion)
    {
        var assetsFile = new AssetsFile
        {
            Header = new AssetsFileHeader
            {
                Version = 22,
                Endianness = false
            },
            Metadata = new AssetsFileMetadata
            {
                UnityVersion = unityVersion,
                UserInformation = string.Empty,
                TargetPlatform = 13, // Android
                TypeTreeEnabled = false,
                TypeTreeTypes = [],
                AssetInfos = [],
                ScriptTypes = [],
                Externals = [],
                RefTypes = []
            }
        };

        using MemoryStream ms = new();
        using var writer = new AssetsFileWriter(ms);
        assetsFile.Write(writer, 0, [], null!);
        return ms.ToArray();
    }

    private static long FindEocdOffset(FileStream fs)
    {
        long maxSearch = Math.Min(fs.Length, 22 + ushort.MaxValue);
        long start = fs.Length - maxSearch;
        fs.Position = start;
        byte[] tail = new byte[(int)maxSearch];
        fs.ReadExactly(tail);

        for (int i = tail.Length - 22; i >= 0; i--)
        {
            if (tail[i] == 0x50 && tail[i + 1] == 0x4b && tail[i + 2] == 0x05 && tail[i + 3] == 0x06)
            {
                int commentLen = tail[i + 20] | (tail[i + 21] << 8);
                if (i + 22 + commentLen == tail.Length)
                    return start + i;
            }
        }

        throw new InvalidOperationException("EOCD not found.");
    }

    private static void AssertAllStoredSoEntriesAre16KbAligned(string apkPath)
    {
        using var fs = File.OpenRead(apkPath);
        using var mem = new Core.Utilities.Signing.FileMemory(fs);
        mem.Position = FindEocdOffset(fs);
        var eocd = new Core.Utilities.Signing.EndOfCentralDirectory(mem);

        mem.Position = eocd.OffsetOfCD;
        int checkedSoCount = 0;
        for (int i = 0; i < eocd.NumberOfCDsOnDisk; i++)
        {
            var cd = new Core.Utilities.Signing.CentralDirectoryFileHeader(mem);
            if (cd.FileName.EndsWith(".so", StringComparison.OrdinalIgnoreCase) && cd.CompressionMethod == 0)
            {
                long savedPos = mem.Position;
                mem.Position = cd.Offset;
                _ = new Core.Utilities.Signing.LocalFileHeader(mem);
                long dataOffset = mem.Position;
                mem.Position = savedPos;

                Assert.True(dataOffset % 16384 == 0,
                    $"Expected stored .so '{cd.FileName}' data offset 0x{dataOffset:X} to be 16KB-aligned (mod 16384 = {dataOffset % 16384}).");
                checkedSoCount++;
            }
        }

        Assert.True(checkedSoCount > 0, "Expected at least one stored .so entry in APK.");
    }

    private static void AssertApkSigningBlockHasV2AndV3(string apkPath)
    {
        using var fs = File.OpenRead(apkPath);
        using var mem = new Core.Utilities.Signing.FileMemory(fs);
        mem.Position = FindEocdOffset(fs);
        var eocd = new Core.Utilities.Signing.EndOfCentralDirectory(mem);

        long cdOffset = eocd.OffsetOfCD;
        Assert.True(cdOffset >= 32, "CD offset too small for APK Signing Block.");

        mem.Position = cdOffset - 16;
        byte[] magic = mem.ReadBytes(16);
        Assert.Equal("APK Sig Block 42", Encoding.ASCII.GetString(magic));

        mem.Position = cdOffset - 24;
        ulong footerSize = mem.ReadULong();
        Assert.True((footerSize + 8) % 4096 == 0, $"Expected APK Signing Block total size ({footerSize + 8}) to be a multiple of 4096.");

        long blockStart = cdOffset - (long)(footerSize + 8);
        Assert.True(blockStart >= 0);
        mem.Position = blockStart;
        ulong headerSize = mem.ReadULong();
        Assert.Equal(footerSize, headerSize);

        var ids = new HashSet<uint>();
        long pairsEnd = cdOffset - 24;
        while (mem.Position < pairsEnd)
        {
            ulong pairLen = mem.ReadULong();
            uint id = mem.ReadUInt();
            ids.Add(id);
            mem.Position += (long)(pairLen - 4);
        }

        Assert.Contains(0x7109871au, ids); // V2
        Assert.Contains(0xf05368c0u, ids); // V3
        Assert.Contains(Core.Utilities.Signing.APKSigningBlock.VERITY_PADDING_BLOCK_ID, ids); // Verity 4KB padding
    }
}
