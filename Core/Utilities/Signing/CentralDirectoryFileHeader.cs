using System;
using System.Text;

namespace MelonLoader.Installer.Core.Utilities.Signing
{
    public class CentralDirectoryFileHeader
    {
        public static readonly int SIGNATURE = 0x02014b50;
        public ushort VersionMadeBy { get; set; }
        public ushort VersionNeeded { get; set; }
        public ushort GeneralPurposeFlag { get; set; }
        public ushort CompressionMethod { get; set; }
        public ushort FileLastModificationTime { get; set; }
        public ushort FileLastModificationDate { get; set; }
        public uint CRC32 { get; set; }
        public uint CompressedSize { get; set; }
        public uint UncompressedSize { get; set; }
        public ushort DiskNumberFileStart { get; set; }
        public ushort InternalFileAttributes { get; set; }
        public uint ExternalFileAttributes { get; set; }
        public uint Offset { get; set; }
        public byte[] FileNameBytes { get; set; }
        public string FileName
        {
            get => Encoding.UTF8.GetString(FileNameBytes);
            set => FileNameBytes = Encoding.UTF8.GetBytes(value ?? "");
        }
        public byte[] ExtraField { get; set; }
        public byte[] FileCommentBytes { get; set; }
        public string FileComment
        {
            get => Encoding.UTF8.GetString(FileCommentBytes);
            set => FileCommentBytes = Encoding.UTF8.GetBytes(value ?? "");
        }

        public CentralDirectoryFileHeader(FileMemory memory)
        {
            int signature = memory.ReadInt();
            if (signature != SIGNATURE)
                throw new Exception("Invalid CentralDirectoryFileHeader signature " + signature.ToString("X4"));
            VersionMadeBy = memory.ReadUShort();
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
            ushort fileCommentLength = memory.ReadUShort();
            DiskNumberFileStart = memory.ReadUShort();
            InternalFileAttributes = memory.ReadUShort();
            ExternalFileAttributes = memory.ReadUInt();
            Offset = memory.ReadUInt();
            FileNameBytes = memory.ReadBytes(fileNameLength);
            ExtraField = memory.ReadBytes(extraFieldLength);
            FileCommentBytes = memory.ReadBytes(fileCommentLength);
        }

        public void Write(FileMemory memory)
        {
            memory.WriteInt(SIGNATURE);
            memory.WriteUShort(VersionMadeBy);
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
            memory.WriteUShort((ushort)FileCommentBytes.Length);
            memory.WriteUShort(DiskNumberFileStart);
            memory.WriteUShort(InternalFileAttributes);
            memory.WriteUInt(ExternalFileAttributes);
            memory.WriteUInt(Offset);
            memory.WriteBytes(FileNameBytes);
            memory.WriteBytes(ExtraField);
            memory.WriteBytes(FileCommentBytes);
        }
    }
}
