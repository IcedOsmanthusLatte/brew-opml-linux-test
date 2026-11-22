package lab.brew.netprobe;

import android.app.Activity;
import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Bundle;
import android.telephony.TelephonyManager;
import java.io.File;
import java.io.FileOutputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

public final class Probe extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        final String nonce = getIntent().getStringExtra("nonce");
        if (nonce == null || !nonce.matches("[a-f0-9]{16}")) {
            finish();
            return;
        }
        new Thread(() -> measure(nonce)).start();
    }

    private void measure(String nonce) {
        try {
            ConnectivityManager cm = (ConnectivityManager)getSystemService(Context.CONNECTIVITY_SERVICE);
            Network active = cm.getActiveNetwork();
            NetworkCapabilities capabilities = active == null ? null : cm.getNetworkCapabilities(active);
            int status = 0;
            String outcome = "ok";
            try {
                URL url = new URL("https://example.com/?brew_probe=" + nonce);
                HttpsURLConnection conn = (HttpsURLConnection)url.openConnection();
                conn.setConnectTimeout(3500);
                conn.setReadTimeout(3500);
                conn.setUseCaches(false);
                conn.setRequestProperty("Cache-Control", "no-cache");
                conn.setInstanceFollowRedirects(false);
                status = conn.getResponseCode();
                conn.disconnect();
            } catch (Exception error) {
                outcome = error.getClass().getSimpleName();
            }
            JSONObject result = new JSONObject();
            result.put("nonce", nonce);
            result.put("http_status", status);
            result.put("outcome", outcome);
            result.put("any_active_network", active != null);
            result.put("wifi", capabilities != null && capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI));
            result.put("cellular", capabilities != null && capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR));
            result.put("validated", capabilities != null && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED));
            TelephonyManager phone = (TelephonyManager)getSystemService(Context.TELEPHONY_SERVICE);
            try {
                result.put("default_mobile_data_enabled", phone.isDataEnabled());
                result.put("mobile_data_connection_allowed", phone.isDataConnectionAllowed());
            } catch (RuntimeException unavailable) {
                result.put("mobile_state_unavailable", unavailable.getClass().getSimpleName());
            }
            File target = new File(getFilesDir(), "proof.json");
            try (FileOutputStream stream = new FileOutputStream(target)) {
                stream.write(result.toString().getBytes(StandardCharsets.UTF_8));
            }
        } catch (Exception ignored) {
            // Missing proof fails the host verifier.
        }
        runOnUiThread(this::finish);
    }
}
