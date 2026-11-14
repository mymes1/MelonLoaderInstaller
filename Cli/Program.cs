using MelonLoader.Installer.Core;
using MelonLoader.Installer.Core.Diagnostics;
using MelonLoader.Installer.Core.Repair;
using System.Text;

namespace MelonLoader.Installer.Cli;

internal sealed class ConsolePatchLogger : IPatchLogger
{
    public void Clear() { }
    public void Log(string message) => Console.WriteLine(message);
}

internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        if (args.Length == 0 || args[0] is "-h" or "--help" or "help")
        {
            PrintUsage();
            return args.Length == 0 ? 1 : 0;
        }

        var logger = new ConsolePatchLogger();
        string cmd = args[0].ToLowerInvariant();

        try
        {
            switch (cmd)
            {
                case "analyze":
                    return await RunAnalyzeAsync(args.Skip(1).ToArray(), logger);
                case "inspect-elf":
                    return RunInspectElf(args.Skip(1).ToArray(), logger);
                case "diagnose":
                    return RunDiagnose(args.Skip(1).ToArray());
                case "repair-melondata":
                    return await RunRepairMelonDataAsync(args.Skip(1).ToArray(), logger);
                case "repair-apk":
                    return await RunRepairApkAsync(args.Skip(1).ToArray(), logger);
                case "patch":
                    return await RunPatchAsync(args.Skip(1).ToArray(), logger);
                case "stage-builds":
                    return await RunStageBuildsAsync(args.Skip(1).ToArray(), logger);
                case "sign":
                    return RunSign(args.Skip(1).ToArray(), logger);
                default:
                    Console.Error.WriteLine($"Unknown command: {cmd}");
                    PrintUsage();
                    return 2;
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[ERROR] {ex}");
            return 1;
        }
    }

    private static void PrintUsage()
    {
        Console.WriteLine("""
            LemonLoader Fork Repair & APK Patching CLI
            Usage:
              LemonLoaderRepairCli analyze <apk-or-bundle> [--online]
              LemonLoaderRepairCli inspect-elf <apk-or-bundle-or-.so>
              LemonLoaderRepairCli diagnose <melon_data.zip-or-patched.apk>
              LemonLoaderRepairCli repair-melondata <input-melon_data.zip> <output-melon_data.zip> [--repaired-bundle <path>]
              LemonLoaderRepairCli repair-apk <patched.apk> --out <output.apk> [--repaired-bundle <path>] [--unity-deps <path>] [--stage <full|no-hooks|minimal-bootstrap|il2cpp-init-only>] [--replace-libunity]
              LemonLoaderRepairCli patch <input.apk-or-bundle> --out <output.apk-or-dir> [--repaired-bundle <path>] [--unity-deps <path>] [--stage <full|no-hooks|minimal-bootstrap|il2cpp-init-only>] [--replace-libunity]
              LemonLoaderRepairCli stage-builds <input.apk-or-bundle> --out <output-dir> [--repaired-bundle <path>] [--unity-deps <path>] [--replace-libunity]
              LemonLoaderRepairCli sign <input.apk> --out <signed.apk>
            """);
    }

    private static int RunInspectElf(string[] args, IPatchLogger logger)
    {
        if (args.Length == 0)
            throw new ArgumentException("Missing APK, bundle, or .so path.");

        string input = args[0];
        if (!File.Exists(input))
            throw new FileNotFoundException("File not found.", input);

        if (input.EndsWith(".so", StringComparison.OrdinalIgnoreCase))
        {
            var elf = ElfHeaderInspector.Inspect(File.ReadAllBytes(input));
            Console.WriteLine($"File:      {input}");
            Console.WriteLine($"Arch:      {elf.ArchitectureName} | Valid={elf.IsValidElf} | Align=0x{elf.MaxLoadSegmentAlignment:X}");
            Console.WriteLine($"SONAME:    {elf.Soname ?? "(none)"}");
            Console.WriteLine($"DT_NEEDED: {(elf.NeededLibraries.Count == 0 ? "(none)" : string.Join(", ", elf.NeededLibraries))}");
            Console.WriteLine($"Exports:   {elf.ExportedSymbols.Count}");
            foreach (var sym in elf.ExportedSymbols.Take(60))
                Console.WriteLine($"  sym: {sym}");
            return elf.IsValidElf ? 0 : 1;
        }

        string? tempDir = null;
        try
        {
            string[] apks;
            if (SplitApkBundleExtractor.IsBundleArchive(input))
            {
                tempDir = Path.Combine(Path.GetTempPath(), "lemon_cli_elf_" + Guid.NewGuid().ToString("N"));
                var extracted = SplitApkBundleExtractor.Extract(input, tempDir);
                apks = extracted.AllApkPaths;
            }
            else
            {
                apks = [input];
            }

            var report = ApkAnalyzer.Analyze(apks);
            logger.Log(report.FormatReport());
            logger.Log(report.FormatElfAndLoadOrderReport());
            return report.Errors.Count == 0 ? 0 : 1;
        }
        finally
        {
            if (tempDir != null && Directory.Exists(tempDir))
                try { Directory.Delete(tempDir, true); } catch { }
        }
    }

    private static async Task<int> RunAnalyzeAsync(string[] args, IPatchLogger logger)
    {
        if (args.Length == 0)
            throw new ArgumentException("Missing APK or bundle path.");

        bool probeOnline = args.Contains("--online", StringComparer.OrdinalIgnoreCase);
        string input = args[0];
        string? tempDir = null;

        try
        {
            string[] apks;
            if (SplitApkBundleExtractor.IsBundleArchive(input))
            {
                tempDir = Path.Combine(Path.GetTempPath(), "lemon_cli_extract_" + Guid.NewGuid().ToString("N"));
                var extracted = SplitApkBundleExtractor.Extract(input, tempDir);
                apks = extracted.AllApkPaths;
            }
            else
            {
                apks = [input];
            }

            var report = ApkAnalyzer.Analyze(apks);
            if (probeOnline)
                await ApkAnalyzer.ProbeOfficialUnityDependenciesAsync(report);

            logger.Log(report.FormatReport());
            logger.Log(report.FormatElfAndLoadOrderReport());
            return report.Errors.Count == 0 ? 0 : 1;
        }
        finally
        {
            if (tempDir != null && Directory.Exists(tempDir))
                try { Directory.Delete(tempDir, true); } catch { }
        }
    }

    private static int RunDiagnose(string[] args)
    {
        if (args.Length == 0)
            throw new ArgumentException("Missing melon_data.zip or patched APK path.");

        var report = LemonDataDiagnostics.Inspect(args[0]);
        Console.WriteLine(report.FormatReport());
        return report.IsHealthy ? 0 : 1;
    }

    private static async Task<int> RunRepairMelonDataAsync(string[] args, IPatchLogger logger)
    {
        if (args.Length < 2)
            throw new ArgumentException("Usage: repair-melondata <input.zip> <output.zip> [--repaired-bundle <path>]");

        string inputZip = args[0];
        string outputZip = args[1];
        string? bundle = GetOption(args, "--repaired-bundle")
            ?? await LemonLoaderRepairer.ResolveOrDownloadRepairedBundleAsync(logger);

        var report = LemonLoaderRepairer.RepairMelonDataZip(inputZip, outputZip, bundle, logger);
        return report.IsHealthy ? 0 : 1;
    }

    private static async Task<int> RunRepairApkAsync(string[] args, IPatchLogger logger)
    {
        if (args.Length < 1)
            throw new ArgumentException("Missing input APK path.");

        string inputApk = args[0];
        string outputApk = GetOption(args, "--out") ?? throw new ArgumentException("Missing --out <output.apk>.");
        string? unityDeps = GetOption(args, "--unity-deps");
        bool replaceLibUnity = args.Contains("--replace-libunity", StringComparer.OrdinalIgnoreCase);
        var stage = LemonPatchStageExtensions.Parse(GetOption(args, "--stage"));
        string bundle = GetOption(args, "--repaired-bundle")
            ?? await LemonLoaderRepairer.ResolveOrDownloadRepairedBundleAsync(logger);

        var report = LemonLoaderRepairer.RepairPatchedApk(
            inputApk,
            outputApk,
            bundle,
            unityDeps,
            logger,
            replaceLibUnity: replaceLibUnity,
            stage: stage);
        return report.IsHealthy ? 0 : 1;
    }

    private static async Task<int> RunPatchAsync(string[] args, IPatchLogger logger)
    {
        if (args.Length < 1)
            throw new ArgumentException("Missing input APK or bundle path.");

        string input = args[0];
        string output = GetOption(args, "--out") ?? throw new ArgumentException("Missing --out <output.apk-or-dir>.");
        string? unityDeps = GetOption(args, "--unity-deps");
        bool replaceLibUnity = args.Contains("--replace-libunity", StringComparer.OrdinalIgnoreCase);
        var stage = LemonPatchStageExtensions.Parse(GetOption(args, "--stage"));
        string bundle = GetOption(args, "--repaired-bundle")
            ?? await LemonLoaderRepairer.ResolveOrDownloadRepairedBundleAsync(logger);

        return PatchSingleTarget(input, output, bundle, unityDeps, replaceLibUnity, stage, logger);
    }

    private static async Task<int> RunStageBuildsAsync(string[] args, IPatchLogger logger)
    {
        if (args.Length < 1)
            throw new ArgumentException("Missing input APK or bundle path.");

        string input = args[0];
        string outDir = GetOption(args, "--out") ?? throw new ArgumentException("Missing --out <output-dir>.");
        string? unityDeps = GetOption(args, "--unity-deps");
        bool replaceLibUnity = args.Contains("--replace-libunity", StringComparer.OrdinalIgnoreCase);
        string bundle = GetOption(args, "--repaired-bundle")
            ?? await LemonLoaderRepairer.ResolveOrDownloadRepairedBundleAsync(logger);

        Directory.CreateDirectory(outDir);
        bool isSplitBundle = SplitApkBundleExtractor.IsBundleArchive(input);

        var stages = new (LemonPatchStage Stage, string Name)[]
        {
            (LemonPatchStage.NoHooks, "stage1-no-hooks"),
            (LemonPatchStage.MinimalBootstrap, "stage2-minimal-bootstrap"),
            (LemonPatchStage.Il2CppInitOnly, "stage3-il2cpp-init-only"),
            (LemonPatchStage.Full, "stage4-full-lemonloader")
        };

        var reportSb = new StringBuilder();
        foreach (var (stage, name) in stages)
        {
            logger.Log($"=== Building {name} ({stage.ToAssetToken()}) ===");
            string target = isSplitBundle
                ? Path.Combine(outDir, name)
                : Path.Combine(outDir, name + ".apk");

            int rc = PatchSingleTarget(input, target, bundle, unityDeps, replaceLibUnity, stage, logger);
            if (rc != 0)
                return rc;

            if (!isSplitBundle)
            {
                var apkReport = ApkAnalyzer.Analyze(target);
                reportSb.AppendLine(apkReport.FormatReport());
                reportSb.AppendLine(apkReport.FormatElfAndLoadOrderReport());
            }
        }

        if (reportSb.Length > 0)
        {
            string reportPath = Path.Combine(outDir, "elf-and-diagnostics-report.txt");
            File.WriteAllText(reportPath, reportSb.ToString(), Encoding.UTF8);
            logger.Log($"Wrote stage ELF & diagnostics report to [ {reportPath} ]");
        }

        return 0;
    }

    private static int PatchSingleTarget(
        string input,
        string output,
        string bundle,
        string? unityDeps,
        bool replaceLibUnity,
        LemonPatchStage stage,
        IPatchLogger logger)
    {
        string tempWork = Path.Combine(Path.GetTempPath(), "lemon_cli_patch_" + Guid.NewGuid().ToString("N"));
        string outDir = Path.Combine(tempWork, "out");
        Directory.CreateDirectory(tempWork);
        Directory.CreateDirectory(outDir);

        try
        {
            string baseApk;
            string libApk = "";
            string[] extraSplits = [];
            bool isSplit = false;

            if (SplitApkBundleExtractor.IsBundleArchive(input))
            {
                var extracted = SplitApkBundleExtractor.Extract(input, Path.Combine(tempWork, "bundle"));
                baseApk = extracted.BaseApkPath;
                libApk = extracted.Arm64SplitApkPath ?? "";
                extraSplits = extracted.ExtraSplitApkPaths;
                isSplit = extracted.AllApkPaths.Length > 1;
            }
            else
            {
                baseApk = input;
            }

            var analysis = ApkAnalyzer.Analyze(isSplit ? [baseApk, .. (string.IsNullOrEmpty(libApk) ? [] : new[] { libApk }), .. extraSplits] : [baseApk]);
            logger.Log(analysis.FormatReport());
            if (!analysis.IsSupportedUnityIl2CppArm64)
                throw new InvalidOperationException("Target APK is not a supported Unity ARM64 IL2CPP app.");

            var patchArgs = new PatchArguments(
                baseApk,
                libApk,
                extraSplits,
                outDir,
                tempWork,
                bundle,
                unityDeps ?? "",
                analysis.UnityVersion,
                analysis.PackageName,
                isSplit,
                replaceLibUnity: replaceLibUnity,
                patchStage: stage);

            var patcher = new Patcher(patchArgs, logger);
            if (!patcher.Run())
                return 1;

            if (!isSplit)
            {
                string? parent = Path.GetDirectoryName(Path.GetFullPath(output));
                if (!string.IsNullOrEmpty(parent))
                    Directory.CreateDirectory(parent);
                File.Copy(patcher.Info.OutputBaseApkPath, output, overwrite: true);
                logger.Log($"Patched APK written to [ {output} ]");

                var diag = LemonDataDiagnostics.Inspect(output);
                logger.Log(diag.FormatReport());
                return diag.IsHealthy ? 0 : 1;
            }
            else
            {
                Directory.CreateDirectory(output);
                foreach (var apk in Directory.GetFiles(outDir, "*.apk"))
                {
                    File.Copy(apk, Path.Combine(output, Path.GetFileName(apk)), overwrite: true);
                }
                logger.Log($"Patched split APKs written to [ {output} ]");
                return 0;
            }
        }
        finally
        {
            try { Directory.Delete(tempWork, true); } catch { }
        }
    }

    private static int RunSign(string[] args, IPatchLogger logger)
    {
        if (args.Length < 1)
            throw new ArgumentException("Missing input APK path.");

        string inputApk = args[0];
        string outputApk = GetOption(args, "--out") ?? throw new ArgumentException("Missing --out <signed.apk>.");
        StandaloneApkSigner.SignApk(inputApk, outputApk, logger);
        return ApkAnalyzer.HasApkV2SignatureBlock(outputApk) ? 0 : 1;
    }

    private static string? GetOption(string[] args, string name)
    {
        for (int i = 0; i < args.Length - 1; i++)
        {
            if (args[i].Equals(name, StringComparison.OrdinalIgnoreCase))
                return args[i + 1];
        }
        return null;
    }
}
