param(
    [ValidateSet('set', 'up', 'down', 'mute', 'unmute')][string]$Action,
    [ValidateRange(0, 100)][int]$Value = 0
)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace LunaAudio {
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    class Enumerator {}
    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IEnumerator {
        void EnumAudioEndpoints(int flow, int mask, out IntPtr devices);
        void GetDefaultAudioEndpoint(int flow, int role, out IDevice device);
    }
    [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IDevice {
        void Activate(ref Guid iid, int context, IntPtr parameters, [MarshalAs(UnmanagedType.IUnknown)] out object instance);
    }
    [ComImport, Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IVolume {
        void RegisterControlChangeNotify(IntPtr notify);
        void UnregisterControlChangeNotify(IntPtr notify);
        void GetChannelCount(out uint count);
        void SetMasterVolumeLevel(float level, ref Guid context);
        void SetMasterVolumeLevelScalar(float level, ref Guid context);
        void GetMasterVolumeLevel(out float level);
        void GetMasterVolumeLevelScalar(out float level);
        void SetChannelVolumeLevel(uint channel, float level, ref Guid context);
        void SetChannelVolumeLevelScalar(uint channel, float level, ref Guid context);
        void GetChannelVolumeLevel(uint channel, out float level);
        void GetChannelVolumeLevelScalar(uint channel, out float level);
        void SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, ref Guid context);
        void GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
    }
    public static class Control {
        public static string Run(string action, int value) {
            var enumerator = (IEnumerator)new Enumerator();
            IDevice device;
            enumerator.GetDefaultAudioEndpoint(0, 1, out device);
            Guid iid = typeof(IVolume).GUID, context = Guid.Empty;
            object instance;
            device.Activate(ref iid, 23, IntPtr.Zero, out instance);
            var volume = (IVolume)instance;
            try {
                float level;
                volume.GetMasterVolumeLevelScalar(out level);
                if (action == "mute" || action == "unmute") {
                    volume.SetMute(action == "mute", ref context);
                } else {
                    float next = action == "set" ? value / 100f : level + (action == "up" ? .1f : -.1f);
                    volume.SetMasterVolumeLevelScalar(Math.Max(0f, Math.Min(1f, next)), ref context);
                }
                bool muted;
                volume.GetMute(out muted);
                volume.GetMasterVolumeLevelScalar(out level);
                return "Volume: " + Math.Round(level * 100) + "% / " + (muted ? "muted" : "unmuted");
            } finally {
                Marshal.ReleaseComObject(instance);
                Marshal.ReleaseComObject(device);
                Marshal.ReleaseComObject(enumerator);
            }
        }
    }
}
'@
[LunaAudio.Control]::Run($Action, $Value)
