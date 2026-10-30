package com.ursmajestic.rush;

import android.content.Context;
import android.os.Bundle;
import android.print.PrintAttributes;
import android.print.PrintDocumentAdapter;
import android.print.PrintManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Matrix;
import android.net.Uri;
import android.util.Base64;
import androidx.exifinterface.media.ExifInterface;
import com.google.mlkit.vision.common.InputImage;
import com.google.mlkit.vision.text.Text;
import com.google.mlkit.vision.text.TextRecognition;
import com.google.mlkit.vision.text.TextRecognizer;
import com.google.mlkit.vision.text.latin.TextRecognizerOptions;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import com.getcapacitor.BridgeActivity;

/**
 * URS Rush: opens admin.ursmajestic.com/rush. An Android WebView ignores
 * window.print(), so the page calls window.URSPrint.print() instead, which
 * hands the page (with its print CSS: one 80 mm slip per station) to the
 * Android print system -- pick the Bluetooth thermal printer there.
 */
public class MainActivity extends BridgeActivity {
    private final ExecutorService ocrWorker = Executors.newSingleThreadExecutor();
    private TextRecognizer recognizer;
    private boolean ocrBusy;
    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        WebView web = getBridge().getWebView();
        web.addJavascriptInterface(new PrintBridge(web), "URSPrint");
        recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS);
        web.addJavascriptInterface(new OCRBridge(web), "URSOCR");
    }

    @Override
    public void onDestroy() {
        ocrWorker.shutdownNow();
        if (recognizer != null) recognizer.close();
        super.onDestroy();
    }

    private class OCRBridge {
        private final WebView web;
        OCRBridge(WebView web) { this.web = web; }

        private boolean trustedPage() {
            Uri uri = Uri.parse(web.getUrl() == null ? "" : web.getUrl());
            return "https".equals(uri.getScheme()) && "admin.ursmajestic.com".equals(uri.getHost())
                && ("/rush".equals(uri.getPath()) || "/purchases".equals(uri.getPath()));
        }

        private void reply(String id, String text, String error) {
            runOnUiThread(() -> {
                if (isFinishing() || isDestroyed() || !trustedPage()) return;
                JSONObject result = new JSONObject();
                try {
                    if (error != null) result.put("error", error);
                    else result.put("text", text);
                } catch (Exception ignored) { return; }
                String script = "window.__rushOCRResult && window.__rushOCRResult(" + JSONObject.quote(id) + "," + result + ")";
                web.evaluateJavascript(script.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"), null);
            });
        }

        @JavascriptInterface
        public void recognize(final String photo, final String id) {
            runOnUiThread(() -> {
                if (!trustedPage() || id == null || id.length() > 80) return;
                if (ocrBusy) { reply(id, null, "Still reading the previous photo. Wait a moment."); return; }
                if (photo == null || photo.length() > 14_000_000) { reply(id, null, "Crop the photo to the slip and try again."); return; }
                ocrBusy = true;
                ocrWorker.execute(() -> {
                    Bitmap bitmap = null;
                    try {
                        int comma = photo.indexOf(',');
                        byte[] bytes = Base64.decode(photo.substring(comma + 1), Base64.DEFAULT);
                        BitmapFactory.Options options = new BitmapFactory.Options();
                        options.inJustDecodeBounds = true;
                        BitmapFactory.decodeByteArray(bytes, 0, bytes.length, options);
                        if (options.outWidth <= 0 || options.outHeight <= 0 || (long) options.outWidth * options.outHeight > 40_000_000)
                            throw new IllegalArgumentException();
                        options.inJustDecodeBounds = false;
                        options.inSampleSize = 1;
                        while (Math.max(options.outWidth, options.outHeight) / options.inSampleSize > 2400) options.inSampleSize *= 2;
                        bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length, options);
                        if (bitmap == null) throw new IllegalArgumentException();
                        ExifInterface exif = new ExifInterface(new ByteArrayInputStream(bytes));
                        Matrix transform = new Matrix();
                        if (exif.isFlipped()) transform.postScale(-1, 1);
                        transform.postRotate(exif.getRotationDegrees());
                        Bitmap oriented = Bitmap.createBitmap(bitmap, 0, 0, bitmap.getWidth(), bitmap.getHeight(), transform, true);
                        if (oriented != bitmap) bitmap.recycle();
                        final Bitmap image = oriented;
                        runOnUiThread(() -> {
                            if (isFinishing() || isDestroyed()) { image.recycle(); return; }
                            recognizer.process(InputImage.fromBitmap(image, 0))
                                .addOnSuccessListener(result -> {
                                    List<Text.Line> lines = new ArrayList<>();
                                    for (Text.TextBlock block : result.getTextBlocks()) lines.addAll(block.getLines());
                                    lines.sort(Comparator.comparingInt((Text.Line line) -> line.getBoundingBox() == null ? 0 : line.getBoundingBox().top)
                                        .thenComparingInt(line -> line.getBoundingBox() == null ? 0 : line.getBoundingBox().left));
                                    StringBuilder text = new StringBuilder();
                                    for (Text.Line line : lines) text.append(line.getText()).append('\n');
                                    reply(id, text.toString(), text.length() == 0 ? "No text found. Move closer and retake the photo." : null);
                                })
                                .addOnFailureListener(error -> reply(id, null, "Couldn't read the photo on this phone. Retake it clearly or enter manually."))
                                .addOnCompleteListener(task -> { ocrBusy = false; image.recycle(); });
                        });
                    } catch (Exception error) {
                        if (bitmap != null && !bitmap.isRecycled()) bitmap.recycle();
                        runOnUiThread(() -> { ocrBusy = false; reply(id, null, "Couldn't open this photo. Use a JPG or PNG and try again."); });
                    }
                });
            });
        }
    }

    private class PrintBridge {
        private final WebView web;

        PrintBridge(WebView web) { this.web = web; }

        @JavascriptInterface
        public void print(final String title) {
            runOnUiThread(() -> {
                PrintManager pm = (PrintManager) getSystemService(Context.PRINT_SERVICE);
                String job = (title == null || title.isEmpty()) ? "Rush slips" : title;
                PrintDocumentAdapter adapter = web.createPrintDocumentAdapter(job);
                PrintAttributes attrs = new PrintAttributes.Builder()
                        .setMediaSize(new PrintAttributes.MediaSize("ROLL80", "80 mm roll", 3150, 11690))
                        .setMinMargins(PrintAttributes.Margins.NO_MARGINS)
                        .build();
                pm.print(job, adapter, attrs);
            });
        }
    }
}
