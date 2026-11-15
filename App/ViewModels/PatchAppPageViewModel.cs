using CommunityToolkit.Maui.Storage;
using MelonLoader.Installer.App.Utils;
using MelonLoader.Installer.Core;
using MelonLoader.Installer.Core.Diagnostics;
using MelonLoader.Installer.Core.Repair;
using System.IO.Compression;
using System.Windows.Input;

namespace MelonLoader.Installer.App.ViewModels;

public class PatchAppPageViewModel : BindableObject
{
    public static UnityApplicationFinder.Data CurrentAppData
    {
        get => _currentAppData ?? _dummyAppData;
        set
        {
            if (PatchRunner.IsPatching)
            {
                PopupHelper.Toast("Currently patching, cannot change apps.").Wait();
                return;
            }

            _currentAppData = value;
        }
    }

    private static readonly UnityApplicationFinder.Data _dummyAppData = new(
        "No app selected.",
        "Select an APK or choose an app from the Apps tab.",
        UnityApplicationFinder.Status.Unpatched,
        UnityApplicationFinder.Source.None,
        [],
        null);

    private static UnityApplicationFinder.Data? _currentAppData;

    private string _diagnosticSummary = "Click 'Analyze APK' or 'Diagnose LemonLoader' to inspect Unity version, ABI, and MelonLoader v0.7.3 Open-Beta dependencies.";
    public string DiagnosticSummary
    {
        get => _diagnosticSummary;
        set
        {
            _diagnosticSummary = value;
            OnPropertyChanged();
        }
    }

    public IReadOnlyList<string> PatchStageOptions { get; } =
    [
        "Full (MelonLoader v0.7.3)",
        "NoHooks (Stock libunity only)",
        "MinimalBootstrap (JNI only)",
        "Il2CppInitOnly (Skip CoreCLR)"
    ];

    public int SelectedPatchStageIndex
    {
        get => PatchRunner.ActivePatchStage switch
        {
            LemonPatchStage.Full => 0,
            LemonPatchStage.NoHooks => 1,
            LemonPatchStage.MinimalBootstrap => 2,
            LemonPatchStage.Il2CppInitOnly => 3,
            _ => 0
        };
        set
        {
            PatchRunner.ActivePatchStage = value switch
            {
                1 => LemonPatchStage.NoHooks,
                2 => LemonPatchStage.MinimalBootstrap,
                3 => LemonPatchStage.Il2CppInitOnly,
                _ => LemonPatchStage.Full
            };
            OnPropertyChanged();
        }
    }

    public bool ReplaceLibUnity
    {
        get => PatchRunner.ReplaceLibUnity;
        set
        {
            if (PatchRunner.ReplaceLibUnity == value)
                return;
            PatchRunner.ReplaceLibUnity = value;
            OnPropertyChanged();
        }
    }

    public ICommand SelectApkCommand { get; }
    public ICommand AnalyzeApkCommand { get; }
    public ICommand DiagnoseLemonCommand { get; }
    public ICommand RepairLemonCommand { get; }
    public ICommand PatchTappedCommand { get; }
    public ICommand SignApkCommand { get; }
    public ICommand SavePatchedApkCommand { get; }
    public ICommand ViewLogsCommand { get; }
    public ICommand PullDeviceLogsCommand { get; }
    public ICommand CustomPatchCommand { get; }
    public ICommand PatchLocalTappedCommand { get; }
    public ICommand RestoreTappedCommand { get; }

    public PatchAppPageViewModel()
    {
        SelectApkCommand = new Command(SelectApkAsync);
        AnalyzeApkCommand = new Command(AnalyzeApkAsync);
        DiagnoseLemonCommand = new Command(DiagnoseLemonAsync);
        RepairLemonCommand = new Command(RepairLemonAsync);
        PatchTappedCommand = new Command(PatchWithoutLocalDeps);
        SignApkCommand = new Command(SignApkAsync);
        SavePatchedApkCommand = new Command(SavePatchedApkAsync);
        ViewLogsCommand = new Command(ViewLogsAsync);
        PullDeviceLogsCommand = new Command(PullDeviceLogsAsync);
        CustomPatchCommand = new Command(SelectCustomPatch);
        PatchLocalTappedCommand = new Command(SelectLocalDepsWithPatch);
        RestoreTappedCommand = new Command(RestoreUnpatchedAPK);

        RefreshLocalSummary();
    }

    public void RefreshLocalSummary()
    {
        if (CurrentAppData.Source == UnityApplicationFinder.Source.File && CurrentAppData.APKPaths.Length > 0)
        {
            try
            {
                var report = ApkAnalyzer.Analyze(CurrentAppData.APKPaths);
                string bundleState = LemonLoaderRepairer.FindLocalRepairedBundle() != null
                    ? "Verified Repaired Bundle Ready"
                    : "Repaired Bundle Not Cached (will fetch/repair on demand)";
                DiagnosticSummary =
                    $"Package: {report.PackageName} | Unity: {report.UnityVersionString} | ABI: {string.Join(", ", report.DetectedAbis)}\n" +
                    $"Target SDK: {report.TargetSdkVersion?.ToString() ?? "?"} | extractNativeLibs: {report.ExtractNativeLibs?.ToString() ?? "unset"} | V2 Sig: {(report.HasV2SignatureBlock ? "Yes" : "No")}\n" +
                    $"LemonLoader: {(report.IsLemonPatched ? "Patched" : "Unpatched")} | {bundleState}";
            }
            catch (Exception ex)
            {
                DiagnosticSummary = $"Selected APK analysis error: {ex.Message}";
            }
        }
        else if (CurrentAppData.Source == UnityApplicationFinder.Source.None)
        {
            string bundleState = LemonLoaderRepairer.FindLocalRepairedBundle() != null
                ? "Verified repaired LemonLoader bundle detected locally."
                : "No local APK selected yet. Use 'Select APK / Bundle' or pick an app from the Apps tab.";
            DiagnosticSummary = bundleState;
        }
    }

    private async void SelectApkAsync()
    {
        FilePickerFileType apkType = new(new Dictionary<DevicePlatform, IEnumerable<string>>
        {
            { DevicePlatform.Android, [ "application/vnd.android.package-archive", "application/zip" ] },
            { DevicePlatform.WinUI, [ ".apk", ".apks", ".xapk", ".zip" ] }
        });

        try
        {
            var result = await FilePicker.Default.PickAsync(new PickOptions
            {
                FileTypes = apkType,
                PickerTitle = "Select Target APK or Split Bundle"
            });

            if (result == null)
                return;

            var appData = await MainPageViewModel.LoadAppDataFromFileAsync(result.FullPath);
            if (appData == null)
                return;

            Plugin.ClearPlugins();
            CurrentAppData = appData;
            RefreshLocalSummary();
            await PopupHelper.Toast($"Loaded {appData.PackageName}");
        }
        catch (Exception ex)
        {
            await PopupHelper.Toast($"Failed to open APK: {ex.Message}");
        }
    }

    private async void AnalyzeApkAsync()
    {
        if (CurrentAppData.Source == UnityApplicationFinder.Source.None || CurrentAppData.APKPaths.Length == 0)
        {
            await PopupHelper.Toast("Please select an APK or app first.");
            return;
        }

        var report = await PatchRunner.BeginAnalyze(CurrentAppData);
        if (report != null)
        {
            DiagnosticSummary =
                $"Unity {report.UnityVersionString} | ABI: {string.Join(", ", report.DetectedAbis)} | Package: {report.PackageName}\n" +
                $"Target SDK: {report.TargetSdkVersion?.ToString() ?? "?"} | extractNativeLibs: {report.ExtractNativeLibs?.ToString() ?? "unset"} | Unity Deps Online: {(report.OfficialUnityDepsAvailable == true ? "Available" : report.OfficialUnityDepsAvailable == false ? "Not Found" : "Unknown")}";
        }
    }

    private async void DiagnoseLemonAsync()
    {
        string? target = null;

        if (CurrentAppData.Source == UnityApplicationFinder.Source.File &&
            CurrentAppData.Status == UnityApplicationFinder.Status.Patched &&
            CurrentAppData.APKPaths.Length > 0)
        {
            target = CurrentAppData.APKPaths[0];
        }
        else if (!string.IsNullOrEmpty(PatchRunner.LastPatchedApkPath) && File.Exists(PatchRunner.LastPatchedApkPath))
        {
            bool inspectLast = await PopupHelper.TwoAnswerQuestion(
                $"Inspect last patched APK?\n{PatchRunner.LastPatchedApkPath}\n\nChoose 'Pick File' to inspect a specific melon_data.zip or APK instead.",
                "Diagnose Target",
                "Inspect Last APK",
                "Pick File");
            if (inspectLast)
                target = PatchRunner.LastPatchedApkPath;
        }

        if (target == null)
        {
            string? localBundle = LemonLoaderRepairer.FindLocalRepairedBundle();
            if (localBundle != null)
            {
                bool useBundle = await PopupHelper.TwoAnswerQuestion(
                    $"Inspect verified local LemonLoader bundle or pick a melon_data.zip / patched APK?",
                    "Diagnose Target",
                    "Inspect Repaired Bundle",
                    "Pick File");
                if (useBundle)
                    target = localBundle;
            }
        }

        if (target == null)
        {
            FilePickerFileType zipOrApk = new(new Dictionary<DevicePlatform, IEnumerable<string>>
            {
                { DevicePlatform.Android, [ "application/zip", "application/vnd.android.package-archive" ] },
                { DevicePlatform.WinUI, [ ".zip", ".apk" ] }
            });
            var picked = await FilePicker.Default.PickAsync(new PickOptions
            {
                FileTypes = zipOrApk,
                PickerTitle = "Select melon_data.zip or Patched APK to Diagnose"
            });
            if (picked == null)
                return;
            target = picked.FullPath;
        }

        var report = await PatchRunner.BeginDiagnose(target);
        if (report != null)
        {
            DiagnosticSummary = report.IsHealthy
                ? $"Diagnostics PASSED for {Path.GetFileName(report.SourcePath)} ({report.Checks.Count} checks)."
                : $"Diagnostics FAILED: NullMethodBug={report.HasNullMethodForMelonLoaderBug}, RegisterTypeOptionsMismatch={report.HasRegisterTypeOptionsMismatch}. Click 'Repair LemonLoader'.";
        }
    }

    private async void RepairLemonAsync()
    {
        bool repairPatchedApk = CurrentAppData.Source == UnityApplicationFinder.Source.File &&
                                CurrentAppData.APKPaths.Length > 0 &&
                                CurrentAppData.Status == UnityApplicationFinder.Status.Patched;

        if (repairPatchedApk)
        {
            bool yesApk = await PopupHelper.TwoAnswerQuestion(
                $"Selected APK appears already patched ({Path.GetFileName(CurrentAppData.APKPaths[0])}).\nRepair this APK in a new *_repaired.apk file, or repair a melon_data.zip bundle?",
                "Repair Mode",
                "Repair Selected APK",
                "Repair Bundle / Zip");
            if (yesApk)
            {
                string srcApk = CurrentAppData.APKPaths[0];
                string outApk = Path.Combine(
                    Path.GetDirectoryName(srcApk)!,
                    Path.GetFileNameWithoutExtension(srcApk) + "_repaired.apk");
                string? res = await PatchRunner.BeginRepairPatchedApk(srcApk, outApk);
                if (res != null)
                    DiagnosticSummary = $"Repaired APK saved: {res}";
                return;
            }
        }

        bool pickBrokenZip = await PopupHelper.TwoAnswerQuestion(
            "Prepare/verify the repaired LemonLoader 0.6.X bundle, or repair an existing broken melon_data.zip (e.g. 0.6.5)?",
            "Repair LemonLoader",
            "Verify / Load Repaired Bundle",
            "Pick melon_data.zip to Repair");

        if (pickBrokenZip)
        {
            string? path = await PatchRunner.BeginRepairBundleOrZip(null);
            if (path != null)
                DiagnosticSummary = $"Active Repaired LemonLoader Bundle: {path}";
            return;
        }

        FilePickerFileType zipType = new(new Dictionary<DevicePlatform, IEnumerable<string>>
        {
            { DevicePlatform.Android, [ "application/zip" ] },
            { DevicePlatform.WinUI, [ ".zip" ] }
        });
        var picked = await FilePicker.Default.PickAsync(new PickOptions
        {
            FileTypes = zipType,
            PickerTitle = "Select Broken melon_data.zip (e.g. 0.6.5) to Repair"
        });
        if (picked == null)
            return;

        string? repairedOut = await PatchRunner.BeginRepairBundleOrZip(picked.FullPath);
        if (repairedOut != null)
            DiagnosticSummary = $"Repaired melon_data.zip saved and activated: {repairedOut}";
    }

    private async void PatchWithoutLocalDeps()
    {
        await DoPatch();
    }

    private async void SignApkAsync()
    {
        string? inputApk = null;
        if (!string.IsNullOrEmpty(PatchRunner.LastPatchedApkPath) && File.Exists(PatchRunner.LastPatchedApkPath))
        {
            bool useLast = await PopupHelper.TwoAnswerQuestion(
                $"Sign last patched APK or pick another APK?\n{PatchRunner.LastPatchedApkPath}",
                "Sign APK",
                "Sign Last APK",
                "Pick APK");
            if (useLast)
                inputApk = PatchRunner.LastPatchedApkPath;
        }

        if (inputApk == null)
        {
            FilePickerFileType apkType = new(new Dictionary<DevicePlatform, IEnumerable<string>>
            {
                { DevicePlatform.Android, [ "application/vnd.android.package-archive" ] },
                { DevicePlatform.WinUI, [ ".apk" ] }
            });
            var picked = await FilePicker.Default.PickAsync(new PickOptions
            {
                FileTypes = apkType,
                PickerTitle = "Select APK to Align and Sign (V2)"
            });
            if (picked == null)
                return;
            inputApk = picked.FullPath;
        }

        string outApk = Path.Combine(
            Path.GetDirectoryName(inputApk)!,
            Path.GetFileNameWithoutExtension(inputApk) + "_signed.apk");

        await PatchRunner.BeginSignApk(inputApk, outApk);
    }

    private async void SavePatchedApkAsync()
    {
        string? sourceApk = PatchRunner.LastPatchedApkPath;
        if (string.IsNullOrEmpty(sourceApk) || !File.Exists(sourceApk))
        {
            if (CurrentAppData.Source == UnityApplicationFinder.Source.File && CurrentAppData.APKPaths.Length > 0)
            {
                string candidate = Path.Combine(
                    Path.GetDirectoryName(CurrentAppData.APKPaths[0])!,
                    Path.GetFileNameWithoutExtension(CurrentAppData.APKPaths[0]) + "_patched.apk");
                if (File.Exists(candidate))
                    sourceApk = candidate;
            }
        }

        if (string.IsNullOrEmpty(sourceApk) || !File.Exists(sourceApk))
        {
            await PopupHelper.Toast("No patched or repaired APK has been generated yet in this session.");
            return;
        }

        try
        {
            using FileStream fs = File.OpenRead(sourceApk);
            var saveResult = await FileSaver.Default.SaveAsync(Path.GetFileName(sourceApk), fs);
            if (saveResult.IsSuccessful)
                await PopupHelper.Alert($"Patched APK saved to:\n{saveResult.FilePath}", "Saved");
            else if (saveResult.Exception != null)
                await PopupHelper.Toast($"Save cancelled or failed: {saveResult.Exception.Message}");
        }
        catch (Exception ex)
        {
            await PopupHelper.Toast($"Failed to save APK: {ex.Message}");
        }
    }

    private async void ViewLogsAsync()
    {
        await PatchRunner.OpenDetailedLogsAsync();
    }

    private async void PullDeviceLogsAsync()
    {
        await PatchRunner.BeginPullDeviceDiagnostics(CurrentAppData.PackageName);
    }

    private async void SelectCustomPatch()
    {
        FilePickerFileType dllType = new(new Dictionary<DevicePlatform, IEnumerable<string>>
        {
            { DevicePlatform.Android, [ "*/*" ] },
            { DevicePlatform.WinUI, [ ".dll" ] }
        });

        var result = await FilePicker.Default.PickAsync(new PickOptions() { FileTypes = dllType });

        if (result == null)
        {
            await PopupHelper.Toast("No file selected.");
            return;
        }

        if (!result.FileName.EndsWith(".dll", StringComparison.OrdinalIgnoreCase))
        {
            await PopupHelper.Toast("Please select a .dll file.");
            return;
        }

        string patchPath = result.FullPath;

        if (!File.Exists(patchPath))
        {
            await PopupHelper.Toast("Selected file does not exist.");
            return;
        }

        if (PatchRunner.IsPatching)
        {
            await PopupHelper.Toast("Already patching or restoring, you cannot work on multiple apps at once.");
            return;
        }

        bool loadResult = Plugin.LoadPlugin(patchPath, out bool added);

        if (!loadResult)
        {
            await PopupHelper.Toast("Selected file is not a valid LemonLoader plugin.");
            return;
        }

        await PopupHelper.Toast((added ? "Added " : "Removed ") + "selected plugin.");
    }

    private async void SelectLocalDepsWithPatch()
    {
        FilePickerFileType zipType = new(new Dictionary<DevicePlatform, IEnumerable<string>>
        {
            { DevicePlatform.Android, [ "application/zip" ] },
            { DevicePlatform.WinUI, [ ".zip" ] }
        });

        try
        {
            var result = await FilePicker.Default.PickAsync(new PickOptions() { FileTypes = zipType });
            if (result != null)
            {
                try
                {
                    using FileStream apkStream = new(result.FullPath, FileMode.Open);
                    using ZipArchive archive = new(apkStream, ZipArchiveMode.Read);

                    bool hasArm64Dir = archive.Entries.Any(a => a.FullName.Contains("arm64-v8a"));

                    if (!hasArm64Dir)
                    {
                        await PopupHelper.Toast("Selected zip does not contain ARM64 libraries and cannot be used.");
                        return;
                    }

                    var unityLib = archive.GetEntry("Libs/arm64-v8a/libunity.so") ?? archive.GetEntry("arm64-v8a/libunity.so");

                    if (unityLib == null)
                    {
                        await PopupHelper.Toast("Selected zip does not contain libunity.so and cannot be used.");
                        return;
                    }

                    await DoPatch(result.FullPath);
                }
                catch (Exception ex)
                {
                    await PopupHelper.Toast("Selected zip is invalid.");
                    System.Diagnostics.Debug.WriteLine(ex);
                    return;
                }
            }
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine(ex);
        }
    }

    private static async Task DoPatch(string? localDepsPath = null)
    {
        if (PatchRunner.IsPatching)
        {
            await PopupHelper.Toast("Already patching or restoring, you cannot work on multiple apps at once.");
            return;
        }

        if (_currentAppData == null || _currentAppData.Source == UnityApplicationFinder.Source.None)
        {
            await PopupHelper.Toast("No app selected.", CommunityToolkit.Maui.Core.ToastDuration.Short);
            return;
        }

        await PatchRunner.Begin(CurrentAppData, localDepsPath);
    }

    private async void RestoreUnpatchedAPK()
    {
        await PatchRunner.BeginRestore(CurrentAppData);
    }
}
