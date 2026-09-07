using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using TMPro;
using UnityEditor;
using UnityEngine;
using UnityEngine.TextCore.LowLevel;

public static class BuildKoreanFont
{
    private const string FontAssetPath = "Assets/Generated/NotoSans-JP.asset";
    private const string BundleName = "aeterna-korean-font";

    public static void Run()
    {
        try
        {
            string fontPath = GetArg("--source-font");
            string charsetPath = GetArg("--charset");
            string outputPath = GetArg("--bundle-output");
            int samplingPointSize = GetOptionalIntArg("--sampling-point-size", 38);
            Build(fontPath, charsetPath, outputPath, samplingPointSize);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            EditorApplication.Exit(1);
        }
    }

    private static void Build(
        string externalFontPath,
        string charsetPath,
        string outputPath,
        int samplingPointSize
    )
    {
        EnsureTmpResources();
        Directory.CreateDirectory("Assets/FontSource");
        Directory.CreateDirectory("Assets/Generated");
        const string importedFontPath = "Assets/FontSource/NotoSansCJKkr-Regular.otf";
        File.Copy(externalFontPath, importedFontPath, true);
        AssetDatabase.ImportAsset(importedFontPath, ImportAssetOptions.ForceSynchronousImport);

        Font sourceFont = AssetDatabase.LoadAssetAtPath<Font>(importedFontPath);
        if (sourceFont == null)
            throw new InvalidOperationException("Unity could not import the source font.");

        uint[] characters = File.ReadAllLines(charsetPath)
            .Where(line => !String.IsNullOrWhiteSpace(line))
            .Select(line => UInt32.Parse(line.Trim(), NumberStyles.HexNumber, CultureInfo.InvariantCulture))
            .Distinct()
            .OrderBy(value => value)
            .ToArray();

        AssetDatabase.DeleteAsset(FontAssetPath);
        TMP_FontAsset fontAsset = TMP_FontAsset.CreateFontAsset(
            sourceFont,
            samplingPointSize,
            5,
            GlyphRenderMode.SDFAA,
            4096,
            4096,
            AtlasPopulationMode.Dynamic,
            false
        );
        if (fontAsset == null)
            throw new InvalidOperationException("TMP_FontAsset.CreateFontAsset returned null.");

        fontAsset.name = "NotoSans-JP";
        fontAsset.atlasTextures[0].name = "NotoSans-JP Atlas";
        fontAsset.material.name = "NotoSans-JP Material";

        AssetDatabase.CreateAsset(fontAsset, FontAssetPath);
        AssetDatabase.AddObjectToAsset(fontAsset.atlasTextures[0], fontAsset);
        AssetDatabase.AddObjectToAsset(fontAsset.material, fontAsset);

        if (!fontAsset.TryAddCharacters(characters, out uint[] missing, false))
        {
            string values = String.Join(", ", missing.Select(value => $"U+{value:X4}"));
            throw new InvalidOperationException($"TMP atlas generation missed characters: {values}");
        }
        if (fontAsset.atlasTextures.Length != 1)
            throw new InvalidOperationException($"Expected one atlas, got {fontAsset.atlasTextures.Length}.");
        if (fontAsset.characterTable.Count != characters.Length)
            throw new InvalidOperationException(
                $"Expected {characters.Length} characters, got {fontAsset.characterTable.Count}."
            );

        fontAsset.atlasPopulationMode = AtlasPopulationMode.Static;
        EditorUtility.SetDirty(fontAsset);
        EditorUtility.SetDirty(fontAsset.atlasTextures[0]);
        EditorUtility.SetDirty(fontAsset.material);
        AssetDatabase.SaveAssets();

        AssetImporter importer = AssetImporter.GetAtPath(FontAssetPath);
        importer.SetAssetBundleNameAndVariant(BundleName, String.Empty);
        AssetDatabase.RemoveUnusedAssetBundleNames();
        AssetDatabase.SaveAssets();

        Directory.CreateDirectory(outputPath);
        AssetBundleManifest manifest = BuildPipeline.BuildAssetBundles(
            outputPath,
            BuildAssetBundleOptions.UncompressedAssetBundle | BuildAssetBundleOptions.ForceRebuildAssetBundle,
            BuildTarget.StandaloneWindows64
        );
        if (manifest == null || !File.Exists(Path.Combine(outputPath, BundleName)))
            throw new InvalidOperationException("Unity did not produce the font AssetBundle.");

        Debug.Log(
            $"KOREAN_FONT_RESULT characters={characters.Length} atlas=4096x4096 " +
            $"samplingPointSize={samplingPointSize} bundle={outputPath}"
        );
        EditorApplication.Exit(0);
    }

    private static void EnsureTmpResources()
    {
        Shader shader = AssetDatabase.LoadAssetAtPath<Shader>(
            "Assets/TextMesh Pro/Shaders/TMP_SDF.shader"
        );
        if (shader == null)
            throw new InvalidOperationException("TMP Essential Resources were not extracted.");
        if (Shader.Find("TextMeshPro/Distance Field") == null)
            throw new InvalidOperationException("TextMeshPro/Distance Field shader is unavailable.");
    }

    private static string GetArg(string name)
    {
        string[] args = Environment.GetCommandLineArgs();
        int index = Array.IndexOf(args, name);
        if (index < 0 || index + 1 >= args.Length)
            throw new ArgumentException($"Missing command-line argument: {name}");
        return Path.GetFullPath(args[index + 1]);
    }

    private static int GetOptionalIntArg(string name, int fallback)
    {
        string[] args = Environment.GetCommandLineArgs();
        int index = Array.IndexOf(args, name);
        if (index < 0)
            return fallback;
        if (index + 1 >= args.Length)
            throw new ArgumentException($"Missing value for command-line argument: {name}");
        if (!Int32.TryParse(args[index + 1], out int value) || value <= 0)
            throw new ArgumentException($"Invalid positive integer for {name}: {args[index + 1]}");
        return value;
    }
}
