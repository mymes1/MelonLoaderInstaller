using System;
using System.Collections.Generic;
using System.Collections.Immutable;
using System.IO;
using System.Linq;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;

namespace MelonLoader.Installer.Core.Diagnostics;

public sealed record AssemblyInspectionResult(
    bool IsValidPe,
    string? AssemblyName,
    Version? AssemblyVersion,
    string? TargetFramework,
    IReadOnlyList<string> TypeNames,
    IReadOnlyList<string> MemberRefNames,
    IReadOnlyList<MethodSignatureInfo> MatchingMethods,
    IReadOnlyList<MemberRefSignatureInfo> MatchingMemberRefs,
    string? Error = null);

public sealed record MethodSignatureInfo(
    string DeclaringType,
    string MethodName,
    string ReturnType,
    IReadOnlyList<string> ParameterTypes);

public sealed record MemberRefSignatureInfo(
    string DeclaringType,
    string MemberName,
    string ReturnType,
    IReadOnlyList<string> ParameterTypes);

public static class DotNetMetadataInspector
{
    public static AssemblyInspectionResult Inspect(
        byte[] peBytes,
        Func<string, string, bool>? methodFilter = null,
        Func<string, string, bool>? memberRefFilter = null)
    {
        try
        {
            using MemoryStream ms = new(peBytes, writable: false);
            using PEReader peReader = new(ms);

            if (!peReader.HasMetadata)
                return new AssemblyInspectionResult(false, null, null, null, [], [], [], [], "PE file has no .NET metadata");

            MetadataReader reader = peReader.GetMetadataReader();
            AssemblyDefinition asmDef = reader.GetAssemblyDefinition();
            string asmName = reader.GetString(asmDef.Name);
            Version asmVersion = asmDef.Version;
            string? targetFramework = ReadTargetFramework(reader);

            var typeNames = new List<string>();
            var matchingMethods = new List<MethodSignatureInfo>();
            var provider = new SignatureStringProvider();

            foreach (TypeDefinitionHandle typeHandle in reader.TypeDefinitions)
            {
                TypeDefinition typeDef = reader.GetTypeDefinition(typeHandle);
                string ns = reader.GetString(typeDef.Namespace);
                string name = reader.GetString(typeDef.Name);
                string fullType = string.IsNullOrEmpty(ns) ? name : $"{ns}.{name}";
                typeNames.Add(fullType);

                if (methodFilter == null)
                    continue;

                foreach (MethodDefinitionHandle mh in typeDef.GetMethods())
                {
                    MethodDefinition md = reader.GetMethodDefinition(mh);
                    string mName = reader.GetString(md.Name);
                    if (!methodFilter(fullType, mName))
                        continue;

                    MethodSignature<string> sig = md.DecodeSignature(provider, genericContext: null);
                    matchingMethods.Add(new MethodSignatureInfo(fullType, mName, sig.ReturnType, sig.ParameterTypes));
                }
            }

            var memberRefNames = new List<string>();
            var matchingMemberRefs = new List<MemberRefSignatureInfo>();

            foreach (MemberReferenceHandle mrh in reader.MemberReferences)
            {
                MemberReference mr = reader.GetMemberReference(mrh);
                string mName = reader.GetString(mr.Name);
                string parentName = ResolveParentName(reader, mr.Parent);
                memberRefNames.Add($"{parentName}::{mName}");

                if (memberRefFilter != null && mr.GetKind() == MemberReferenceKind.Method && memberRefFilter(parentName, mName))
                {
                    MethodSignature<string> sig = mr.DecodeMethodSignature(provider, genericContext: null);
                    matchingMemberRefs.Add(new MemberRefSignatureInfo(parentName, mName, sig.ReturnType, sig.ParameterTypes));
                }
            }

            return new AssemblyInspectionResult(
                true,
                asmName,
                asmVersion,
                targetFramework,
                typeNames,
                memberRefNames,
                matchingMethods,
                matchingMemberRefs);
        }
        catch (Exception ex)
        {
            return new AssemblyInspectionResult(false, null, null, null, [], [], [], [], ex.Message);
        }
    }

    private static string? ReadTargetFramework(MetadataReader reader)
    {
        foreach (CustomAttributeHandle cah in reader.GetAssemblyDefinition().GetCustomAttributes())
        {
            CustomAttribute ca = reader.GetCustomAttribute(cah);
            string attrType = ResolveConstructorParentName(reader, ca.Constructor);
            if (attrType == "System.Runtime.Versioning.TargetFrameworkAttribute")
            {
                try
                {
                    BlobReader br = reader.GetBlobReader(ca.Value);
                    if (br.ReadUInt16() == 1)
                        return br.ReadSerializedString();
                }
                catch { }
            }
        }
        return null;
    }

    private static string ResolveConstructorParentName(MetadataReader reader, EntityHandle ctorHandle)
    {
        if (ctorHandle.Kind == HandleKind.MemberReference)
        {
            MemberReference mr = reader.GetMemberReference((MemberReferenceHandle)ctorHandle);
            return ResolveParentName(reader, mr.Parent);
        }
        if (ctorHandle.Kind == HandleKind.MethodDefinition)
        {
            MethodDefinition md = reader.GetMethodDefinition((MethodDefinitionHandle)ctorHandle);
            return ResolveTypeDefName(reader, md.GetDeclaringType());
        }
        return string.Empty;
    }

    private static string ResolveParentName(MetadataReader reader, EntityHandle handle)
    {
        return handle.Kind switch
        {
            HandleKind.TypeReference => ResolveTypeRefName(reader, (TypeReferenceHandle)handle),
            HandleKind.TypeDefinition => ResolveTypeDefName(reader, (TypeDefinitionHandle)handle),
            HandleKind.TypeSpecification => "<TypeSpec>",
            HandleKind.ModuleReference => reader.GetString(reader.GetModuleReference((ModuleReferenceHandle)handle).Name),
            HandleKind.MethodDefinition => ResolveConstructorParentName(reader, handle),
            _ => handle.Kind.ToString()
        };
    }

    private static string ResolveTypeRefName(MetadataReader reader, TypeReferenceHandle handle)
    {
        TypeReference tr = reader.GetTypeReference(handle);
        string ns = reader.GetString(tr.Namespace);
        string name = reader.GetString(tr.Name);
        if (tr.ResolutionScope.Kind == HandleKind.TypeReference)
            return $"{ResolveTypeRefName(reader, (TypeReferenceHandle)tr.ResolutionScope)}+{name}";
        return string.IsNullOrEmpty(ns) ? name : $"{ns}.{name}";
    }

    private static string ResolveTypeDefName(MetadataReader reader, TypeDefinitionHandle handle)
    {
        if (handle.IsNil)
            return string.Empty;
        TypeDefinition td = reader.GetTypeDefinition(handle);
        string ns = reader.GetString(td.Namespace);
        string name = reader.GetString(td.Name);
        if (td.IsNested)
            return $"{ResolveTypeDefName(reader, td.GetDeclaringType())}+{name}";
        return string.IsNullOrEmpty(ns) ? name : $"{ns}.{name}";
    }

    private sealed class SignatureStringProvider : ISignatureTypeProvider<string, object?>
    {
        public string GetArrayType(string elementType, ArrayShape shape) => $"{elementType}[{new string(',', Math.Max(0, shape.Rank - 1))}]";
        public string GetByReferenceType(string elementType) => $"{elementType}&";
        public string GetFunctionPointerType(MethodSignature<string> signature) => $"fnptr({string.Join(",", signature.ParameterTypes)})->{signature.ReturnType}";
        public string GetGenericInstantiation(string genericType, ImmutableArray<string> typeArguments) => $"{genericType}<{string.Join(", ", typeArguments)}>";
        public string GetGenericMethodParameter(object? genericContext, int index) => $"!!{index}";
        public string GetGenericTypeParameter(object? genericContext, int index) => $"!{index}";
        public string GetModifiedType(string modifier, string unmodifiedType, bool isRequired)
            => $"{(isRequired ? "modreq" : "modopt")}({modifier}) {unmodifiedType}";
        public string GetPinnedType(string elementType) => $"pinned {elementType}";
        public string GetPointerType(string elementType) => $"{elementType}*";
        public string GetSZArrayType(string elementType) => $"{elementType}[]";
        public string GetTypeFromDefinition(MetadataReader reader, TypeDefinitionHandle handle, byte rawTypeKind)
            => $"[self]{ResolveTypeDefName(reader, handle)}";
        public string GetTypeFromReference(MetadataReader reader, TypeReferenceHandle handle, byte rawTypeKind)
        {
            TypeReference tr = reader.GetTypeReference(handle);
            string scope = tr.ResolutionScope.Kind == HandleKind.AssemblyReference
                ? reader.GetString(reader.GetAssemblyReference((AssemblyReferenceHandle)tr.ResolutionScope).Name)
                : "";
            string typeName = ResolveTypeRefName(reader, handle);
            return string.IsNullOrEmpty(scope) ? typeName : $"[{scope}]{typeName}";
        }
        public string GetTypeFromSpecification(MetadataReader reader, object? genericContext, TypeSpecificationHandle handle, byte rawTypeKind)
            => reader.GetTypeSpecification(handle).DecodeSignature(this, genericContext);

        public string GetPrimitiveType(PrimitiveTypeCode typeCode) => typeCode switch
        {
            PrimitiveTypeCode.Void => "void",
            PrimitiveTypeCode.Boolean => "bool",
            PrimitiveTypeCode.Char => "char",
            PrimitiveTypeCode.SByte => "int8",
            PrimitiveTypeCode.Byte => "uint8",
            PrimitiveTypeCode.Int16 => "int16",
            PrimitiveTypeCode.UInt16 => "uint16",
            PrimitiveTypeCode.Int32 => "int32",
            PrimitiveTypeCode.UInt32 => "uint32",
            PrimitiveTypeCode.Int64 => "int64",
            PrimitiveTypeCode.UInt64 => "uint64",
            PrimitiveTypeCode.Single => "float32",
            PrimitiveTypeCode.Double => "float64",
            PrimitiveTypeCode.String => "string",
            PrimitiveTypeCode.Object => "object",
            PrimitiveTypeCode.IntPtr => "native int",
            PrimitiveTypeCode.UIntPtr => "native uint",
            PrimitiveTypeCode.TypedReference => "typedref",
            _ => typeCode.ToString()
        };
    }
}
