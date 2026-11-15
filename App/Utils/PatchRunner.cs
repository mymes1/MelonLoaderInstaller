using AdvancedSharpAdbClient.Exceptions;
using MelonLoader.Installer.App.Views;
using MelonLoader.Installer.Core;
using MelonLoader.Installer.Core.Diagnostics;
using MelonLoader.Installer.Core.Repair;
using Newtonsoft.Json.Linq;
using System.Diagnostics;
using System.Text;
using UnityVersion = AssetRipper.Primitives.UnityVersion;

namespace MelonLoader.Installer.App.Utils;

public static class PatchRunner
{
    public static bool IsPatching => _isPatching;
    public static string? LastPatchedApkPath { get; private set; }
    public static string? LastLogPath => _logger?.LogPath ?? FindLatestLogPath();
    public static string LastConsoleText { get; private set; } = "";
    public static string? ActiveRepairedMelonDataPath { get; set; }
    public static bool ReplaceLibUnity { get; set; } = false;
    public static LemonPatchStage ActivePatchStage { get; set; } = LemonPatchStage.Full;

    private static PatchLogger? _logger = null;
    private static PatchingConsolePage? _consolePage = null;

    private static string _basePath = "";
    private static string _tempPath = "";

    private static string _apkOutputPath = "";
    private static string _melonDataPath = "";
    private static string _unityDepsPath = "";

    private static UnityVersion _unityVersion = UnityVersion.MinVersion;

    private static bool _isPatching = false;

    public static string GetBaseAppDataPath()
    {
#if ANDROID
        return Path.Combine(Android.OS.Environment.ExternalStorageDirectory!.ToString(), "MelonLoader.Installer.App");
#else
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "MelonLoader.Installer.App");
#endif
    }

    public static string? FindLatestLogPath()
    {
        string logDir = Path.Combine(GetBaseAppDataPath(), "Logs");
        if (!Directory.Exists(logDir))
            return null;

        return Directory.GetFiles(logDir, "*.log")
            .OrderByDescending(File.GetLastWriteTimeUtc)
            .FirstOrDefault();
    }

    public static string GetCurrentOrLatestLogContent()
    {
        if (!string.IsNullOrWhiteSpace(LastConsoleText))
            return LastConsoleText;

        string? latest = FindLatestLogPath();
        if (latest != null && File.Exists(latest))
            return File.ReadAllText(latest);

        return "No diagnostic or patching logs recorded yet.";
    }

    private static async Task PrepareConsoleAsync(string title)
    {
        await Shell.Current.GoToAsync(nameof(PatchingConsolePage));
        _consolePage = (PatchingConsolePage)Shell.Current.CurrentPage;
        _consolePage.TopTitle = title;
        _consolePage.BackButtonVisible = false;
        _basePath = GetBaseAppDataPath();
        _logger = new(_consolePage, _basePath);
        _logger.Clear();
        _logger.Log($"Created Log File [ {_logger.LogPath} ]");
    }

    public static async Task OpenDetailedLogsAsync()
    {
        await Shell.Current.GoToAsync(nameof(PatchingConsolePage));
        var page = (PatchingConsolePage)Shell.Current.CurrentPage;
        page.TopTitle = "Detailed Logs";
        page.BackButtonVisible = !_isPatching;
        page.Log = GetCurrentOrLatestLogContent();
    }

    public static async Task BeginPullDeviceDiagnostics(string? packageName)
    {
        if (_isPatching)
            return;

        _isPatching = true;
        try
        {
            Reset();
            await PrepareConsoleAsync("Pulling ADB Device Diagnostics...");

            await Task.Run(async () =>
            {
                string outDir = Path.Combine(GetBaseAppDataPath(), "DeviceLogs");
                var (hasDevice, rawPath, summary) = await ADBManager.CaptureDeviceDiagnosticsAsync(packageName, outDir);
                _logger?.Log(summary);
                if (hasDevice)
                    _logger?.Log($"Saved full raw ADB logcat & crash bundle to [ {rawPath} ]");
            });
        }
        catch (Exception ex)
        {
            _logger?.Log("[ERROR] " + ex);
        }
        finally
        {
            _isPatching = false;
            if (_consolePage != null)
                MainThread.BeginInvokeOnMainThread(() => _consolePage.BackButtonVisible = true);
        }
    }

    public static async Task<ApkAnalysisReport?> BeginAnalyze(UnityApplicationFinder.Data data)
    {
        if (_isPatching)
            return null;

        _isPatching = true;
        ApkAnalysisReport? result = null;

        try
        {
            Reset();
            await PrepareConsoleAsync("Analyzing APK...");

            result = await Task.Run(async () =>
            {
                string[] paths = data.APKPaths;
                string? tempPullDir = null;

                if (data.Source == UnityApplicationFinder.Source.ADB)
                {
                    tempPullDir = Path.Combine(_basePath, "analyze_temp", data.PackageName);
                    if (Directory.Exists(tempPullDir))
                        Directory.Delete(tempPullDir, true);
                    Directory.CreateDirectory(tempPullDir);

                    var pulled = new List<string>();
                    foreach (string remoteApk in data.APKPaths)
                    {
                        string localApk = Path.Combine(tempPullDir, Path.GetFileName(remoteApk));
                        _logger?.Log($"Pulling [ {remoteApk} ]...");
                        await ADBManager.PullFileToPath(remoteApk, localApk);
                        pulled.Add(localApk);
                    }
                    paths = pulled.ToArray();
                }

                var report = ApkAnalyzer.Analyze(paths);
                _logger?.Log("Checking official Unity dependencies availability...");
                await ApkAnalyzer.ProbeOfficialUnityDependenciesAsync(report);
                _logger?.Log(report.FormatReport());
                _logger?.Log(report.FormatElfAndLoadOrderReport());
                return report;
            });
        }
        catch (Exception ex)
        {
            _logger?.Log("[ERROR] " + ex);
        }
        finally
        {
            _isPatching = false;
            if (_consolePage != null)
                MainThread.BeginInvokeOnMainThread(() => _consolePage.BackButtonVisible = true);
        }

        return result;
    }

    public static async Task<LemonDiagnosticReport?> BeginDiagnose(string targetPath)
    {
        if (_isPatching)
            return null;

        _isPatching = true;
        LemonDiagnosticReport? report = null;

        try
        {
            Reset();
            await PrepareConsoleAsync("Diagnosing LemonLoader...");

            report = await Task.Run(() =>
            {
                _logger?.Log($"Inspecting [ {targetPath} ]...");
                var diag = LemonDataDiagnostics.Inspect(targetPath);
                _logger?.Log(diag.FormatReport());
                return diag;
            });
        }
        catch (Exception ex)
        {
            _logger?.Log("[ERROR] " + ex);
        }
        finally
        {
            _isPatching = false;
            if (_consolePage != null)
                MainThread.BeginInvokeOnMainThread(() => _consolePage.BackButtonVisible = true);
        }

        return report;
    }

    public static async Task<string?> BeginRepairBundleOrZip(string? inputZipToRepair = null)
    {
        if (_isPatching)
            return null;

        _isPatching = true;
        string? outputZip = null;

        try
        {
            Reset();
            await PrepareConsoleAsync("Repairing LemonLoader...");

            outputZip = await Task.Run(async () =>
            {
                string repairedBundle = await LemonLoaderRepairer.ResolveOrDownloadRepairedBundleAsync(_logger!);
                if (string.IsNullOrEmpty(inputZipToRepair))
                {
                    var diag = LemonDataDiagnostics.Inspect(repairedBundle);
                    _logger?.Log(diag.FormatReport());
                    ActiveRepairedMelonDataPath = repairedBundle;
                    return repairedBundle;
                }

                string outPath = Path.Combine(
                    Path.GetDirectoryName(inputZipToRepair) ?? GetBaseAppDataPath(),
                    Path.GetFileNameWithoutExtension(inputZipToRepair) + "_repaired.zip");

                LemonLoaderRepairer.RepairMelonDataZip(inputZipToRepair, outPath, repairedBundle, _logger!);
                ActiveRepairedMelonDataPath = outPath;
                return outPath;
            });

            await MainThread.InvokeOnMainThreadAsync(async () =>
            {
                if (outputZip != null)
                    await PopupHelper.Alert($"LemonLoader dependencies verified/repaired:\n{outputZip}", "Repair Complete");
            });
        }
        catch (Exception ex)
        {
            _logger?.Log("[ERROR] " + ex);
            await MainThread.InvokeOnMainThreadAsync(async () =>
                await PopupHelper.Alert($"Repair failed: {ex.Message}", "Repair Failed"));
        }
        finally
        {
            _isPatching = false;
            if (_consolePage != null)
                MainThread.BeginInvokeOnMainThread(() => _consolePage.BackButtonVisible = true);
        }

        return outputZip;
    }

    public static async Task<string?> BeginRepairPatchedApk(string inputApkPath, string outputApkPath, string? localUnityDepsPath = null)
    {
        if (_isPatching)
            return null;

        _isPatching = true;
        string? finalPath = null;

        try
        {
            Reset();
            await PrepareConsoleAsync("Repairing Patched APK...");

            await Task.Run(async () =>
            {
                string repairedBundle = ActiveRepairedMelonDataPath != null && File.Exists(ActiveRepairedMelonDataPath)
                    ? ActiveRepairedMelonDataPath
                    : await LemonLoaderRepairer.ResolveOrDownloadRepairedBundleAsync(_logger!);

                var report = LemonLoaderRepairer.RepairPatchedApk(
                    inputApkPath,
                    outputApkPath,
                    repairedBundle,
                    localUnityDepsPath,
                    _logger!,
                    replaceLibUnity: ReplaceLibUnity,
                    stage: ActivePatchStage);
                if (!report.IsHealthy)
                    throw new InvalidOperationException("Repaired APK still has critical diagnostic issues.");

                finalPath = outputApkPath;
                LastPatchedApkPath = outputApkPath;
            });

            await MainThread.InvokeOnMainThreadAsync(async () =>
                await PopupHelper.Alert($"Successfully repaired & signed APK:\n{finalPath}", "Repair Complete"));
        }
        catch (Exception ex)
        {
            _logger?.Log("[ERROR] " + ex);
            await MainThread.InvokeOnMainThreadAsync(async () =>
                await PopupHelper.Alert($"APK repair failed: {ex.Message}", "Repair Failed"));
        }
        finally
        {
            _isPatching = false;
            if (_consolePage != null)
                MainThread.BeginInvokeOnMainThread(() => _consolePage.BackButtonVisible = true);
        }

        return finalPath;
    }

    public static async Task<bool> BeginSignApk(string inputApkPath, string outputApkPath)
    {
        if (_isPatching)
            return false;

        _isPatching = true;
        bool ok = false;

        try
        {
            Reset();
            await PrepareConsoleAsync("Signing APK...");

            await Task.Run(() =>
            {
                StandaloneApkSigner.SignApk(inputApkPath, outputApkPath, _logger!);
                ok = ApkAnalyzer.HasApkV2SignatureBlock(outputApkPath);
                _logger?.Log($"V2 Signature Block Present: {ok}");
                if (ok)
                    LastPatchedApkPath = outputApkPath;
            });

            await MainThread.InvokeOnMainThreadAsync(async () =>
                await PopupHelper.Alert(ok ? $"APK aligned and V2-signed:\n{outputApkPath}" : "APK signing failed.", ok ? "Success" : "Failure"));
        }
        catch (Exception ex)
        {
            _logger?.Log("[ERROR] " + ex);
            await MainThread.InvokeOnMainThreadAsync(async () =>
                await PopupHelper.Alert($"Signing failed: {ex.Message}", "Failure"));
        }
        finally
        {
            _isPatching = false;
            if (_consolePage != null)
                MainThread.BeginInvokeOnMainThread(() => _consolePage.BackButtonVisible = true);
        }

        return ok;
    }

    public static async Task Begin(UnityApplicationFinder.Data data, string? localUnityDepsPath)
    {
        _isPatching = true;

        try
        {
            Reset();

            await PrepareConsoleAsync("Patching...");

            string packageName = data.Source == UnityApplicationFinder.Source.File
                ? (string.IsNullOrWhiteSpace(data.PackageName) ? "LocalFile" : data.PackageName)
                : data.PackageName;
            _tempPath = Path.Combine(_basePath, "patch_temp", packageName);

            _melonDataPath = Path.Combine(_tempPath, "melondata.zip");
            _unityDepsPath = Path.Combine(_tempPath, "unity.bin");

            _apkOutputPath = Path.Combine(_tempPath, "output");

            if (Directory.Exists(_tempPath))
                Directory.Delete(_tempPath, true);

            Task task = Task.Run(async () => await InternalBegin(data, localUnityDepsPath));
            await task;
        }
        catch (Exception ex)
        {
            Debug.WriteLine(ex);
            _logger?.Log(ex.ToString());
            MarkFailure();
        }

        _isPatching = false;
        if (_consolePage != null)
            MainThread.BeginInvokeOnMainThread(() => _consolePage.BackButtonVisible = true);
    }

    private static async Task InternalBegin(UnityApplicationFinder.Data data, string? localUnityDepsPath)
    {
        _logger?.Log($"Build Directory: [ {_basePath} ]");

        if (Directory.Exists(_tempPath))
            Directory.Delete(_tempPath, true);

        await PrepareAssets(localUnityDepsPath);

        await GetUnityVersion(data);

        await BackupAPKs(data);

        await CopyAPKsFromDevice(data);

        await BackupAppData(data);

        CallPatchCore(data);

        if (!await ReinstallApp(data))
        {
            MarkFailure();
            return;
        }

        await RestoreAppData(data);

        MarkSuccess();
    }

    public static async Task BeginRestore(UnityApplicationFinder.Data data)
    {
        _isPatching = true;

        try
        {
            Reset();
            await PrepareConsoleAsync("Restoring...");

            Task task = Task.Run(async () => await InternalBeginRestore(data));
            await task;
        }
        catch (Exception ex)
        {
            Debug.WriteLine(ex);
            _logger?.Log(ex.ToString());
            MarkFailure();
        }

        _isPatching = false;
        if (_consolePage != null)
            MainThread.BeginInvokeOnMainThread(() => _consolePage.BackButtonVisible = true);
    }

    private static async Task InternalBeginRestore(UnityApplicationFinder.Data data)
    {
        await BackupAppData(data);

        string? backupDir = data.GetBackupDirectory();
        if (backupDir == null)
            return;

        APKInstaller installer = new(data, _logger!);

        if (await installer.Install(backupDir))
        {
            await MainThread.InvokeOnMainThreadAsync(async () =>
            {
                await PopupHelper.Alert("Successfully restored the application.", "Success");
                _consolePage!.BackButtonVisible = true;
            });

            await RestoreAppData(data);
        }
        else
        {
            await MainThread.InvokeOnMainThreadAsync(async () =>
            {
                await PopupHelper.Alert("Unable to restore the application, read the console for more info.", "Failure");
                _consolePage!.BackButtonVisible = true;
            });
        }
    }

    private static async Task GetUnityVersion(UnityApplicationFinder.Data data)
    {
        _logger?.Log("Parsing application Unity version");

        _unityVersion = await UnityVersionFinder.ParseUnityVersion(data, _tempPath);
        if (_unityVersion == UnityVersion.MinVersion)
        {
            throw new Exception("Failed to parse Unity version");
        }

        _logger?.Log($"Found [ {_unityVersion} ]");
    }

    private static async Task PrepareAssets(string? localUnityDepsPath)
    {
        _logger?.Log("Preparing assets");

        Directory.CreateDirectory(_tempPath);
        Directory.CreateDirectory(_apkOutputPath);

        _logger?.Log("Created required directories");

        if (localUnityDepsPath != null && File.Exists(localUnityDepsPath))
        {
            File.Copy(localUnityDepsPath, _unityDepsPath);
            _logger?.Log("Local dependencies provided; copied");
        }

        _logger?.Log("Preparing repaired LemonLoader data");

        if (!await DownloadMelonData(_melonDataPath))
            throw new Exception("Failed to obtain MelonLoader data");
    }

    private static async Task<bool> DownloadMelonData(string destination)
    {
        // 1. Prefer user-selected or verified repaired bundle
        if (!string.IsNullOrEmpty(ActiveRepairedMelonDataPath) && File.Exists(ActiveRepairedMelonDataPath))
        {
            var check = LemonDataDiagnostics.Inspect(ActiveRepairedMelonDataPath);
            if (check.IsHealthy)
            {
                _logger?.Log($"Using active repaired LemonLoader bundle [ {ActiveRepairedMelonDataPath} ]");
                File.Copy(ActiveRepairedMelonDataPath, destination, overwrite: true);
                return true;
            }
        }

        try
        {
            string repaired = await LemonLoaderRepairer.ResolveOrDownloadRepairedBundleAsync(_logger!);
            ActiveRepairedMelonDataPath = repaired;
            File.Copy(repaired, destination, overwrite: true);
            return true;
        }
        catch (Exception ex)
        {
            _logger?.Log($"Repaired bundle not yet available ({ex.Message}). Checking official LemonLoader release...");
        }

#if DEBUG
        if (File.Exists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Desktop), "melon_data/melon_data.zip")))
        {
            _logger?.Log("Using local melon data");
            File.Copy(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Desktop), "melon_data/melon_data.zip"), destination, true);
            return true;
        }

        if (File.Exists(@"/sdcard/Download/melon_data.zip"))
        {
            _logger?.Log("Using local melon data");
            File.Copy(@"/sdcard/Download/melon_data.zip", destination, true);
            return true;
        }
#endif

        _logger?.Log("Retrieving release info from GitHub (LemonLoader/MelonLoader)");

        try
        {
            using HttpClient client = new();
            client.DefaultRequestHeaders.Add("User-Agent", "MelonLoaderInstaller/1.0");

            string releaseInfo = await client.GetStringAsync("https://api.github.com/repos/LemonLoader/MelonLoader/releases/latest");
            JObject baseJson = JObject.Parse(releaseInfo);
            JToken asset = baseJson["assets"]!
                .First(a => a["name"]!.ToString().StartsWith("melon_data"));
            string assetUrl = asset["browser_download_url"]!.ToString();

            _logger?.Log($"Downloading [ {assetUrl} ]");
            byte[] data = await client.GetByteArrayAsync(assetUrl);
            await File.WriteAllBytesAsync(destination, data);

            var diag = LemonDataDiagnostics.Inspect(destination);
            _logger?.Log(diag.FormatReport());
            if (!diag.IsHealthy)
            {
                _logger?.Log("[WARNING] Downloaded upstream melon_data.zip contains known 0.6.5 defects. Use 'Repair LemonLoader' with a repaired bundle to avoid runtime crashes.");
            }
            _logger?.Log("Done");
        }
        catch (Exception ex)
        {
            _logger?.Log("Failed to get release info from GitHub, aborting install.\n" + ex.ToString());
            return false;
        }

        return true;
    }

    private static async Task BackupAPKs(UnityApplicationFinder.Data data)
    {
        if (data.Source == UnityApplicationFinder.Source.File)
            return;

        if (data.Status == UnityApplicationFinder.Status.Patched)
        {
            _logger?.Log("App was previously patched, skipping back up");
            return;
        }

        _logger?.Log("Backing up APKs");

        string backupDir = data.GetBackupDirectory()!;

        if (!Directory.Exists(backupDir))
            Directory.CreateDirectory(backupDir);

        foreach (string apk in data.APKPaths)
        {
            _logger?.Log($"Backing up [ {apk} ]");
            string backupPath = Path.Combine(backupDir, Path.GetFileName(apk));
            if (data.Source == UnityApplicationFinder.Source.PackageManager)
                File.Copy(apk, backupPath, true);
            else
                await ADBManager.PullFileToPath(apk, backupPath);
        }
    }

    private static async Task CopyAPKsFromDevice(UnityApplicationFinder.Data data)
    {
        if (data.Source != UnityApplicationFinder.Source.ADB)
            return;

        _logger?.Log("Copying APKs from device");

        foreach (string apk in data.APKPaths)
        {
            _logger?.Log($"Copying [ {apk} ]");
            await ADBManager.PullFileToPath(apk, Path.Combine(_apkOutputPath, Path.GetFileName(apk)));
        }
    }

    private static bool CallPatchCore(UnityApplicationFinder.Data data)
    {
        _logger?.Log("Starting patching core");

        IEnumerable<string> inputPaths = data.Source == UnityApplicationFinder.Source.ADB
            ? data.APKPaths.Select(p => Path.Combine(_apkOutputPath, Path.GetFileName(p)))
            : data.APKPaths;

        var layout = SplitApkBundleExtractor.ResolveApkLayout(inputPaths);
        string targetApkPath = layout.BaseApkPath;
        string libraryApkPath = layout.Arm64SplitApkPath ?? "";
        string[] extraSplits = layout.ExtraSplitApkPaths;

        _logger?.Log($"Resolved APK layout: base={Path.GetFileName(targetApkPath)}, arm64Split={(string.IsNullOrEmpty(libraryApkPath) ? "(none - in base)" : Path.GetFileName(libraryApkPath))}, extraSplits={extraSplits.Length}");

        PatchArguments arguments = new(
            targetApkPath,
            libraryApkPath,
            extraSplits,
            _apkOutputPath,
            _tempPath,
            _melonDataPath,
            _unityDepsPath,
            _unityVersion,
            data.PackageName,
            layout.AllApkPaths.Length > 1,
            replaceLibUnity: ReplaceLibUnity,
            patchStage: ActivePatchStage);
        Patcher patcher = new(arguments, _logger!);

        if (!patcher.Run())
        {
            throw new Exception("Failed to patch.");
        }

        return true;
    }

    private static async Task BackupAppData(UnityApplicationFinder.Data data)
    {
        if (data.Source != UnityApplicationFinder.Source.ADB)
            return;

        _logger?.Log("Backing up app data, this can take awhile");

        string src = $"/sdcard/Android/data/{data.PackageName}";
        string dest = Path.Combine(_tempPath, "data_backup");

        try
        {
            await ADBManager.PullDirectoryToPath(src, dest, true, _logger);
        }
        catch (AdbException ex)
        {
            _logger?.Log($"Failed to back up data directory.\n{ex}");
            bool res = await PopupHelper.TwoAnswerQuestion("Backing up game data failed, you may lose any save data or additional game data if the patching continues. Do you want to continue anyway?", "Unable to Back Up Data", "Yes", "No");
            if (!res)
                throw;
        }

        _logger?.Log("Backing up app assets, this can take awhile");

        src = $"/sdcard/Android/obb/{data.PackageName}";
        dest = $"/sdcard/Android/{data.PackageName}.obb.lemon";

        await ADBManager.TryMoveUsingScript(src, dest);
    }

    private static async Task RestoreAppData(UnityApplicationFinder.Data data)
    {
        if (data.Source != UnityApplicationFinder.Source.ADB)
            return;

        _logger?.Log("Restoring app assets (OBBs), this can take awhile");

        string src = $"/sdcard/Android/{data.PackageName}.obb.lemon";
        string dest = $"/sdcard/Android/obb/{data.PackageName}";

        await ADBManager.TryMoveUsingScript(src, dest);

        _logger?.Log("Asset restore complete");

        string dataBackupPath = Path.Combine(_tempPath, "data_backup", $"{data.PackageName}");
        if (Directory.Exists(dataBackupPath) && Directory.GetFileSystemEntries(dataBackupPath).Length > 0)
        {
            await Application.Current!.Dispatcher.DispatchAsync(async () =>
            {
                bool res = await PopupHelper.TwoAnswerQuestion("Lemon is unable to restore app data, do you want to open the folder containing the back up now? It will not be deleted after patching is complete.", "Unable to Restore", "Yes", "No");
                if (res)
                {
                    Process proc = new()
                    {
                        StartInfo =
                        {
                            FileName = "explorer.exe",
                            Arguments = $"/select,\"{dataBackupPath}\""
                        }
                    };

                    proc.Start();
                }
            });
        }
    }

    private static async Task<bool> ReinstallApp(UnityApplicationFinder.Data data)
    {
        if (data.Source == UnityApplicationFinder.Source.File)
        {
#if WINDOWS
            _logger?.Log("APK patched successfully, moving to final path");

            string originalPath = data.APKPaths.First();
            string[] outputApks = Directory.GetFiles(_apkOutputPath, "*.apk");
            if (outputApks.Length == 1)
            {
                string newPatchedPath = Path.Combine(Path.GetDirectoryName(originalPath)!, Path.GetFileNameWithoutExtension(originalPath) + "_patched.apk");
                File.Move(outputApks[0], newPatchedPath, true);
                LastPatchedApkPath = newPatchedPath;
                _logger?.Log($"Patched APK saved [ {newPatchedPath} ]");

                var diag = LemonDataDiagnostics.Inspect(newPatchedPath);
                _logger?.Log(diag.FormatReport());

                Process proc = new()
                {
                    StartInfo =
                    {
                        FileName = "explorer.exe",
                        Arguments = $"/select,\"{newPatchedPath}\""
                    }
                };
                proc.Start();
            }
            else
            {
                string outFolder = Path.Combine(Path.GetDirectoryName(originalPath)!, Path.GetFileNameWithoutExtension(originalPath) + "_patched_splits");
                Directory.CreateDirectory(outFolder);
                foreach (var apk in outputApks)
                    File.Move(apk, Path.Combine(outFolder, Path.GetFileName(apk)), true);
                LastPatchedApkPath = Path.Combine(outFolder, "base.apk");
                _logger?.Log($"Patched split APKs saved to [ {outFolder} ]");
            }
#else
            _logger?.Log("APK patched successfully.");
            _logger?.Log($"Patched APK(s) are available at [ {_apkOutputPath} ]");
#endif

            return true;
        }

        _logger?.Log("Application patched successfully, reinstalling");

        APKInstaller installer = new(data, _logger!);
        return await installer.Install(_apkOutputPath);
    }

    private static void MarkSuccess()
    {
        Application.Current!.Dispatcher.Dispatch(async () =>
        {
            _logger?.Log("Patching complete");

            await PopupHelper.Alert("Successfully patched the application.", "Success");

            _consolePage!.BackButtonVisible = true;

            _isPatching = false;
        });
    }

    private static void MarkFailure()
    {
        Application.Current!.Dispatcher.Dispatch(async () =>
        {
            _logger?.Log("Patching failed.");

            await PopupHelper.Alert("Unable to patch the application, read the console for more info.", "Failure");

            _consolePage!.BackButtonVisible = true;

            _isPatching = false;
        });
    }

    private static void Reset()
    {
        _basePath = "";
        _tempPath = "";
        _melonDataPath = "";
        _apkOutputPath = "";
        _unityDepsPath = "";
        _unityVersion = UnityVersion.MinVersion;
        LastConsoleText = "";
    }

    public class PatchLogger : IPatchLogger
    {
        public string LogPath => _logPath;

        private PatchingConsolePage _consolePage;
        private string _logPath;

        private FileStream _fileStream;
        private StreamWriter _writer;

        public PatchLogger(PatchingConsolePage consolePage, string basePath)
        {
            string baseLogPath = Path.Combine(basePath, "Logs");
            if (!Directory.Exists(baseLogPath))
                Directory.CreateDirectory(baseLogPath);

            _consolePage = consolePage;
            _logPath = Path.Combine(baseLogPath, $"{DateTime.Now:%y-%M-%d_%H-%m-%s}.log");

            _fileStream = File.Open(_logPath, new FileStreamOptions() { Access = FileAccess.ReadWrite, BufferSize = 0, Mode = FileMode.Create, Share = FileShare.ReadWrite });
            _writer = new StreamWriter(_fileStream, Encoding.UTF8, 1)
            {
                AutoFlush = true
            };
        }

        public void Clear()
        {
            LastConsoleText = "";
            Application.Current!.Dispatcher.Dispatch(() => _consolePage.Log = "");
        }

        public void Log(string message)
        {
            Debug.WriteLine(message);
            _writer.WriteLine(message);
            LastConsoleText += message + '\n';
            Application.Current!.Dispatcher.Dispatch(() => _consolePage.Log += message + '\n');
        }
    }
}
