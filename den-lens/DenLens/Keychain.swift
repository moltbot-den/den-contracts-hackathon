import Foundation
import Security

/// Agent API key storage. The key is typed once in Settings and never ships in the binary.
enum Keychain {
    private static let service = "com.moltbotden.denlens"
    private static let account = "agent-api-key"
    private static let assAccount = "ass-device-token"

    private static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    /// Device-bound token from Agent Secret Store pairing. It lists and decides requests only with a Secure Enclave signature.
    static func loadASSToken() -> String? { load(assAccount) }
    @discardableResult static func saveASSToken(_ token: String) -> Bool { save(token, assAccount) }

    static func loadAPIKey() -> String? { load(account) }

    private static func load(_ account: String) -> String? {
        var q = query(account)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func saveAPIKey(_ key: String) -> Bool { save(key, account) }

    private static func save(_ key: String, _ account: String) -> Bool {
        SecItemDelete(query(account) as CFDictionary)
        let trimmed = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return true }
        var q = query(account)
        q[kSecValueData as String] = Data(trimmed.utf8)
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(q as CFDictionary, nil) == errSecSuccess
    }
}
