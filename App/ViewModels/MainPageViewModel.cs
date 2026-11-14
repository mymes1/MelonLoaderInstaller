using MelonLoader.Installer.App.Utils;
using MelonLoader.Installer.App.Views;
using MelonLoader.Installer.Core;
using MelonLoader.Installer.Core.Diagnostics;
using MelonLoader.Installer.Core.Repair;
using System.Collections.ObjectModel;
using System.Windows.Input;

namespace MelonLoader.Installer.App.ViewModels;

public class MainPageViewModel : BindableObject
{
    public ObservableCollection<UnityApplicationFinder.Data> Items { get; protected set; } = [];

    public ICommand ItemTappedCommand { get; }
    public ICommand SelectAPKButtonCommand { get; }

    public Action? OnAppAddingComplete { get; set; }
    public Action? OnAppAddingReset { get; set; }

    private CancellationTokenSource? _appSearchTokenSource = null;
    private Thread? _appSearchThread = null;

    public MainPageViewModel()
    {
        ItemTappedCommand = new Command<UnityApplicationFinder.Data>(OnItemTapped);
        SelectAPKButtonCommand = new Command(OnTapSelectAPKButton);
        OnAppAddingComplete = null;
        OnAppAddingReset = null;

        try
        {
            ADBManager.OnPrimaryDeviceChanged += StartNewAppSearchThread;
        }
        catch { }

        StartNewAppSearchThread();
    }

    private void StartNewAppSearchThread()
    {
        _appSearchTokenSource?.Cancel();
        _appSearchThread?.Join();

        _appSearchTokenSource = new();
        _appSearchThread = new(() => AddAllApps(_appSearchTokenSource.Token));
        _appSearchThread.Start();
    }

    private void AddAllApps(CancellationToken token = default)
    {
        Application.Current!.Dispatcher.Dispatch(() => OnAppAddingReset?.Invoke());

        Items.ClearOnUI();
        UnityApplicationFinder.Reset();
        foreach (var app in UnityApplicationFinder.Find(token))
        {
            if (token.IsCancellationRequested)
            {
                Items.ClearOnUI();
                return;
            }

            Items.AddOnUI(app);
        }

        if (token.IsCancellationRequested)
        {
            Items.ClearOnUI();
            return;
        }

        Application.Current!.Dispatcher.Dispatch(() => OnAppAddingComplete?.Invoke());
    }

    private async void OnTapSelectAPKButton()
    {
        FilePickerFileType apkType = new(new Dictionary<DevicePlatform, IEnumerable<string>>
        {
            { DevicePlatform.Android, [ "application/vnd.android.package-archive", "application/zip" ] },
            { DevicePlatform.WinUI, [ ".apk", ".apks", ".xapk", ".zip" ] }
        });

        try
        {
            var result = await FilePicker.Default.PickAsync(new PickOptions()
            {
                FileTypes = apkType,
                PickerTitle = "Select Android APK or Split Bundle (.apk / .apks / .xapk)"
            });

            if (result == null)
                return;

            var appData = await LoadAppDataFromFileAsync(result.FullPath);
            if (appData == null)
                return;

            Plugin.ClearPlugins();
            PatchAppPageViewModel.CurrentAppData = appData;
            Shell.Current.GoToTabOnFirst(nameof(PatchAppPage));
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine(ex);
        }
    }

    public static async Task<UnityApplicationFinder.Data?> LoadAppDataFromFileAsync(string filePath)
    {
        try
        {
            string[] apkPaths;
            if (SplitApkBundleExtractor.IsBundleArchive(filePath))
            {
                string extractDir = Path.Combine(
                    PatchRunner.GetBaseAppDataPath(),
                    "split_extract",
                    Path.GetFileNameWithoutExtension(filePath));
                var extracted = await Task.Run(() => SplitApkBundleExtractor.Extract(filePath, extractDir));
                apkPaths = extracted.AllApkPaths;
            }
            else
            {
                apkPaths = [filePath];
            }

            var report = await Task.Run(() => ApkAnalyzer.Analyze(apkPaths));

            if (!report.HasArm64)
            {
                await PopupHelper.Toast("Selected APK does not support ARM64 (arm64-v8a) and cannot be patched.");
                return null;
            }

            if (!report.HasLibUnity)
            {
                await PopupHelper.Toast("Selected APK is not a Unity app (missing libunity.so).");
                return null;
            }

            if (!report.HasLibIl2Cpp)
            {
                await PopupHelper.Toast("Selected APK is not IL2CPP (missing libil2cpp.so).");
                return null;
            }

            var status = report.IsLemonPatched
                ? UnityApplicationFinder.Status.Patched
                : UnityApplicationFinder.Status.Unpatched;

            string displayName = string.IsNullOrWhiteSpace(report.PackageName)
                ? Path.GetFileNameWithoutExtension(filePath)
                : $"{Path.GetFileNameWithoutExtension(filePath)} (Unity {report.UnityVersionString})";

            return new UnityApplicationFinder.Data(
                displayName,
                report.PackageName,
                status,
                UnityApplicationFinder.Source.File,
                apkPaths,
                null);
        }
        catch (IOException ex)
        {
            await PopupHelper.Toast("Failed to access the selected APK.");
            System.Diagnostics.Debug.WriteLine(ex);
            return null;
        }
        catch (Exception ex)
        {
            await PopupHelper.Toast($"Selected APK is invalid or unsupported: {ex.Message}");
            System.Diagnostics.Debug.WriteLine(ex);
            return null;
        }
    }

    private async void OnItemTapped(UnityApplicationFinder.Data item)
    {
        if (item.Status == UnityApplicationFinder.Status.Unsupported)
        {
            await PopupHelper.Toast("This app is unsupported.");
            return;
        }

        if (!AndroidPermissionHandler.HaveRequired())
        {
            await PopupHelper.Toast("Permissions are not setup.");
            Shell.Current.GoToTabOnFirst(nameof(PermissionSetupPage));
            return;
        }

        PatchAppPageViewModel.CurrentAppData = item;
        Shell.Current.GoToTabOnFirst(nameof(PatchAppPage));
    }
}
