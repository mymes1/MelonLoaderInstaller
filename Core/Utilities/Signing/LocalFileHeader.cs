using System;
using System.Text;

namespace MelonLoader.Installer.Core.Utilities.Signing
{
    public class LocalFileHeader
    {
        public static readonly int SIGNATURE = 0x04034b50;

        public ushort VersionNeeded { get; set; }
        public ushort GeneralPurposeFlag { get; set; }
        public ushort CompressionMethod { get; set; }
        public ushort FileLastModificationTime { get; set; }
        public ushort FileLastModificationDate { get; set; }
        public uint CRC32 { get; set; }
        public uint CompressedSize { get; set; }
        public uint UncompressedSize { get; set; }
        public byte[] FileNameBytes { get; set; }
        public string FileName
        {
            get => Encoding.UTF8.GetString(FileNameBytes);
            set => FileNameBytes = Encoding.UTF8.GetBytes(value ?? "");
        }
        public byte[] ExtraField { get; set; }

        public LocalFileHeader(FileMemory memory)
        {
            int signature = memory.ReadInt();
            if (signature != SIGNATURE)
                throw new Exception("Invalid LocalFileHeader signature " + signature.ToString("X4"));
            VersionNeeded = memory.ReadUShort();
            GeneralPurposeFlag = memory.ReadUShort();
            CompressionMethod = memory.ReadUShort();
            FileLastModificationTime = memory.ReadUShort();
            FileLastModificationDate = memory.ReadUShort();
            CRC32 = memory.ReadUInt();
            CompressedSize = memory.ReadUInt();
            UncompressedSize = memory.ReadUInt();
            ushort fileNameLength = memory.ReadUShort();
            ushort extraFieldLength = memory.ReadUShort();
            FileNameBytes = memory.ReadBytes(fileNameLength);
            ExtraField = memory.ReadBytes(extraFieldLength);
        }

        public void Write(FileMemory memory)
        {
            memory.WriteInt(SIGNATURE);
            memory.WriteUShort(VersionNeeded);
            memory.WriteUShort(GeneralPurposeFlag);
            memory.WriteUShort(CompressionMethod);
            memory.WriteUShort(FileLastModificationTime);
            memory.WriteUShort(FileLastModificationDate);
            memory.WriteUInt(CRC32);
            memory.WriteUInt(CompressedSize);
            memory.WriteUInt(UncompressedSize);
            memory.WriteUShort((ushort)FileNameBytes.Length);
            memory.WriteUShort((ushort)ExtraField.Length);
            memory.WriteBytes(FileNameBytes);
            memory.WriteBytes(ExtraField);
        }
    }
}
