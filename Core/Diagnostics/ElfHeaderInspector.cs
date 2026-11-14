using System;
using System.IO;
using System.Text;

namespace MelonLoader.Installer.Core.Diagnostics;

public sealed record ElfInspectionResult(
    bool IsValidElf,
    bool Is64Bit,
    bool IsLittleEndian,
    ushort Machine,
    string ArchitectureName,
    ulong MaxLoadSegmentAlignment,
    long FileSizeBytes,
    string? Error = null)
{
    public bool IsArm64 => IsValidElf && Is64Bit && Machine == 0xB7;
}

public static class ElfHeaderInspector
{
    public const ushort EM_386 = 0x03;
    public const ushort EM_ARM = 0x28;
    public const ushort EM_X86_64 = 0x3E;
    public const ushort EM_AARCH64 = 0xB7;

    public static ElfInspectionResult Inspect(byte[] data)
    {
        if (data.Length < 64)
            return new ElfInspectionResult(false, false, false, 0, "Invalid", 0, data.Length, "File too small to be ELF");

        if (data[0] != 0x7F || data[1] != (byte)'E' || data[2] != (byte)'L' || data[3] != (byte)'F')
            return new ElfInspectionResult(false, false, false, 0, "NotELF", 0, data.Length, "Missing ELF magic bytes");

        bool is64 = data[4] == 2;
        bool isLittleEndian = data[5] == 1;
        if (!isLittleEndian)
            return new ElfInspectionResult(true, is64, false, 0, "BigEndian", 0, data.Length, "Unexpected big-endian ELF");

        ushort machine = BitConverter.ToUInt16(data, 18);
        string arch = machine switch
        {
            EM_AARCH64 => "arm64-v8a (AArch64)",
            EM_ARM => "armeabi-v7a (ARM32)",
            EM_X86_64 => "x86_64",
            EM_386 => "x86",
            _ => $"Unknown (0x{machine:X4})"
        };

        ulong maxAlign = 0;
        if (is64 && data.Length >= 64)
        {
            ulong ePhoff = BitConverter.ToUInt64(data, 32);
            ushort ePhentsize = BitConverter.ToUInt16(data, 54);
            ushort ePhnum = BitConverter.ToUInt16(data, 56);

            if (ePhentsize >= 56 && ePhoff > 0 && ePhoff + (ulong)ePhnum * ePhentsize <= (ulong)data.Length)
            {
                for (int i = 0; i < ePhnum; i++)
                {
                    int off = (int)(ePhoff + (ulong)i * ePhentsize);
                    uint pType = BitConverter.ToUInt32(data, off);
                    if (pType == 1) // PT_LOAD
                    {
                        ulong pAlign = BitConverter.ToUInt64(data, off + 48);
                        if (pAlign > maxAlign)
                            maxAlign = pAlign;
                    }
                }
            }
        }

        return new ElfInspectionResult(true, is64, isLittleEndian, machine, arch, maxAlign, data.Length);
    }

    public static ElfInspectionResult InspectStream(Stream stream)
    {
        using MemoryStream ms = new();
        stream.CopyTo(ms);
        return Inspect(ms.ToArray());
    }

    public static bool ContainsAscii(byte[] data, string needle)
    {
        byte[] pattern = Encoding.ASCII.GetBytes(needle);
        return data.AsSpan().IndexOf(pattern) >= 0;
    }

    public static bool ContainsUtf16(byte[] data, string needle)
    {
        byte[] pattern = Encoding.Unicode.GetBytes(needle);
        return data.AsSpan().IndexOf(pattern) >= 0;
    }
}
