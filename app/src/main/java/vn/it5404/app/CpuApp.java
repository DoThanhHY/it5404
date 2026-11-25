package vn.it5404.app;

import java.net.InetAddress;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Map;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@SpringBootApplication
@RestController
public class CpuApp {

    public static void main(String[] args) {
        SpringApplication.run(CpuApp.class, args);
    }

    /** Endpoint nhẹ: trả về hostname để kiểm tra cân bằng tải. */
    @GetMapping("/")
    public Map<String, String> hello() throws Exception {
        return Map.of("host", InetAddress.getLocalHost().getHostName());
    }

    /** Endpoint tốn CPU: băm SHA-256 lặp lại {@code n} lần (mặc định 20000 ≈ vài chục ms). */
    @GetMapping("/work")
    public Map<String, Object> work(@RequestParam(defaultValue = "20000") int n) throws Exception {
        n = Math.min(Math.max(n, 1), 1_000_000);
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] buf = "it5404".getBytes();
        for (int i = 0; i < n; i++) {
            buf = md.digest(buf);
        }
        return Map.of("host", InetAddress.getLocalHost().getHostName(),
                      "iterations", n,
                      "digest", HexFormat.of().formatHex(buf, 0, 8));
    }
}
