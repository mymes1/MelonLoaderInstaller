using CommunityToolkit.Maui.Storage;
using MelonLoader.Installer.App.Utils;
using System.Diagnostics;
using System.Text;

namespace MelonLoader.Installer.App.Views;

public partial class PatchingConsolePage : ContentPage
{
    public string Log
    {
        get => LogLabel.Text ?? "";
        set
        {
            LogLabel.Text = value;
            LogScrollView.ScrollToAsync(LogLabel, ScrollToPosition.End, false);
        }
    }

    public string TopTitle
    {
        get => TitleLabel.Text ?? "";
        set => TitleLabel.Text = value;
    }

    public bool BackButtonVisible { get => GoBackButton.IsVisible; set => GoBackButton.IsVisible = value; }

    private readonly Command _goBackCommand;

    public PatchingConsolePage()
    {
        InitializeComponent();
        _goBackCommand = new(GoBack);

        GoBackButton.IsVisible = false;
        GoBackButton.GestureRecognizers.Add(new TapGestureRecognizer { Command = _goBackCommand });
        CopyLogButton.GestureRecognizers.Add(new TapGestureRecognizer { Command = new Command(CopyLogAsync) });
        SaveLogButton.GestureRecognizers.Add(new TapGestureRecognizer { Command = new Command(SaveLogAsync) });
        OpenLogsFolderButton.GestureRecognizers.Add(new TapGestureRecognizer { Command = new Command(OpenLogsFolder) });
    }

    private async void CopyLogAsync()
    {
        await Clipboard.Default.SetTextAsync(Log);
        await PopupHelper.Toast("Diagnostic log copied to clipboard.");
    }

    private async void SaveLogAsync()
    {
        try
        {
            using var ms = new MemoryStream(Encoding.UTF8.GetBytes(Log));
            var res = await FileSaver.Default.SaveAsync($"lemon_diagnostics_{DateTime.Now:yyyyMMdd_HHmmss}.log", ms);
            if (res.IsSuccessful)
                await PopupHelper.Toast($"Log saved to {res.FilePath}");
        }
        catch (Exception ex)
        {
            await PopupHelper.Toast($"Failed to save log: {ex.Message}");
        }
    }

    private async void OpenLogsFolder()
    {
        string logDir = Path.Combine(PatchRunner.GetBaseAppDataPath(), "Logs");
        Directory.CreateDirectory(logDir);
#if WINDOWS
        Process.Start(new ProcessStartInfo
        {
            FileName = "explorer.exe",
            Arguments = $"\"{logDir}\"",
            UseShellExecute = true
        });
#else
        await PopupHelper.Alert($"Logs are saved at:\n{logDir}", "Logs Directory");
#endif
    }

    private async void GoBack()
    {
        Shell.Current.GoToTabOnFirst(nameof(PatchAppPage));
        await Navigation.PopAsync();
    }

    protected override bool OnBackButtonPressed()
    {
        return false;
    }
}
