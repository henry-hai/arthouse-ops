<?php
/**
 * Plugin Name: ArtHouse Ops Connector
 * Description: Read-only REST endpoints that expose Visual Form Builder Pro form entries as JSON for the arthouse-ops automation. Reads entries directly from the database, bypassing the broken VFB Pro entries screen. Access requires an authenticated administrator, for example via a WordPress Application Password.
 * Version: 0.3.0
 * Author: Henry Hai Nguyen
 */

if (!defined('ABSPATH')) {
    exit;
}

add_action('rest_api_init', function () {
    register_rest_route('arthouse-ops/v1', '/schema', array(
        'methods'             => 'GET',
        'callback'            => 'arthouse_ops_schema',
        'permission_callback' => 'arthouse_ops_can',
    ));
    register_rest_route('arthouse-ops/v1', '/entries', array(
        'methods'             => 'GET',
        'callback'            => 'arthouse_ops_entries',
        'permission_callback' => 'arthouse_ops_can',
        'args'                => array(
            'form'         => array('required' => false),
            'limit'        => array('required' => false),
            'offset'       => array('required' => false),
            'include_spam' => array('required' => false),
        ),
    ));
});

// Only authenticated administrators may read entries.
function arthouse_ops_can() {
    return current_user_can('manage_options');
}

function arthouse_ops_vfb_tables() {
    global $wpdb;
    $tables = $wpdb->get_col($wpdb->prepare('SHOW TABLES LIKE %s', '%vfb%'));
    return $tables ? $tables : array();
}

function arthouse_ops_fields_table() {
    foreach (arthouse_ops_vfb_tables() as $t) {
        if (stripos($t, 'field') !== false) {
            return $t;
        }
    }
    return null;
}

// Map field id => human label for a form, read from the VFB fields table.
function arthouse_ops_label_map($form) {
    global $wpdb;
    $map = array();
    $table = arthouse_ops_fields_table();
    if (!$table) {
        return $map;
    }
    $safe = '`' . esc_sql($table) . '`';
    $rows = $wpdb->get_results($wpdb->prepare('SELECT id, data FROM ' . $safe . ' WHERE form_id = %d', intval($form)), ARRAY_A);
    foreach ($rows as $r) {
        $label = 'field-' . $r['id'];
        $d = maybe_unserialize($r['data']);
        if (is_array($d)) {
            if (!empty($d['label'])) {
                $label = $d['label'];
            } elseif (!empty($d['name'])) {
                $label = $d['name'];
            }
        }
        $map[(string) $r['id']] = trim(wp_strip_all_tags($label));
    }
    return $map;
}

// GET /entries?form=3&limit=200&offset=0&include_spam=0
function arthouse_ops_entries($request) {
    global $wpdb;

    $form = intval($request->get_param('form'));
    if (!$form) {
        return new WP_Error('no_form', 'Provide a form id, for example ?form=3', array('status' => 400));
    }
    $limit = intval($request->get_param('limit'));
    if ($limit <= 0) { $limit = 200; }
    if ($limit > 1000) { $limit = 1000; }
    $offset = max(0, intval($request->get_param('offset')));
    $include_spam = filter_var($request->get_param('include_spam'), FILTER_VALIDATE_BOOLEAN);

    $label_map = arthouse_ops_label_map($form);

    // How many entries this form has, and how many are flagged spam.
    $total = (int) $wpdb->get_var($wpdb->prepare(
        "SELECT COUNT(*) FROM {$wpdb->posts} p
         INNER JOIN {$wpdb->postmeta} m ON p.ID = m.post_id
         WHERE p.post_type = 'vfb_entry' AND m.meta_key = '_vfb_form_id' AND m.meta_value = %d",
        $form
    ));
    $spam_total = (int) $wpdb->get_var($wpdb->prepare(
        "SELECT COUNT(*) FROM {$wpdb->posts} p
         INNER JOIN {$wpdb->postmeta} f ON p.ID = f.post_id AND f.meta_key = '_vfb_form_id' AND f.meta_value = %d
         INNER JOIN {$wpdb->postmeta} s ON p.ID = s.post_id AND s.meta_key = 'vfbp-spam' AND s.meta_value <> '0' AND s.meta_value <> ''
         WHERE p.post_type = 'vfb_entry'",
        $form
    ));

    // A page of entry post ids, newest first.
    $ids = $wpdb->get_col($wpdb->prepare(
        "SELECT p.ID FROM {$wpdb->posts} p
         INNER JOIN {$wpdb->postmeta} m ON p.ID = m.post_id
         WHERE p.post_type = 'vfb_entry' AND m.meta_key = '_vfb_form_id' AND m.meta_value = %d
         ORDER BY p.post_date DESC
         LIMIT %d OFFSET %d",
        $form, $limit, $offset
    ));

    $entries = array();
    foreach ($ids as $pid) {
        $post = get_post($pid);
        $meta = get_post_meta($pid);
        $fields = array();
        $entry_id = '';
        $seq = '';
        $is_spam = 0;
        foreach ($meta as $key => $vals) {
            $val = is_array($vals) ? reset($vals) : $vals;
            if ($key === '_vfb_entry_id') {
                $entry_id = $val;
            } elseif ($key === '_vfb_seq_num') {
                $seq = $val;
            } elseif ($key === 'vfbp-spam') {
                $is_spam = ($val !== '' && $val !== '0') ? 1 : 0;
            } elseif (strpos($key, '_vfb_field-') === 0) {
                $fid = substr($key, strlen('_vfb_field-'));
                $label = isset($label_map[$fid]) ? $label_map[$fid] : ('field-' . $fid);
                $dv = maybe_unserialize($val);
                if (is_array($dv)) {
                    $dv = implode(', ', array_map('strval', $dv));
                }
                $fields[$label] = $dv;
            }
        }
        if (!$include_spam && $is_spam) {
            continue;
        }
        $entries[] = array(
            'entry_id' => $entry_id !== '' ? $entry_id : (string) $pid,
            'seq_num'  => $seq,
            'date'     => $post ? $post->post_date : '',
            'is_spam'  => $is_spam,
            'fields'   => $fields,
        );
    }

    return rest_ensure_response(array(
        'form'        => $form,
        'total'       => $total,
        'spam_total'  => $spam_total,
        'legit_total' => $total - $spam_total,
        'returned'    => count($entries),
        'limit'       => $limit,
        'offset'      => $offset,
        'field_map'   => $label_map,
        'entries'     => $entries,
    ));
}

// GET /schema : discovery helper (tables, post types, VFB structure).
function arthouse_ops_schema($request) {
    global $wpdb;
    $out = array('prefix' => $wpdb->prefix);

    $status = $wpdb->get_results('SHOW TABLE STATUS', ARRAY_A);
    $all = array();
    if ($status) {
        foreach ($status as $s) {
            $all[] = array('table' => $s['Name'], 'approx_rows' => (int) $s['Rows']);
        }
        usort($all, function ($a, $b) { return $b['approx_rows'] - $a['approx_rows']; });
    }
    $out['biggest_tables'] = array_slice($all, 0, 20);
    $out['post_types'] = $wpdb->get_results(
        "SELECT post_type, COUNT(*) AS n FROM {$wpdb->posts} GROUP BY post_type ORDER BY n DESC",
        ARRAY_A
    );
    $out['vfb_tables'] = array();
    foreach (arthouse_ops_vfb_tables() as $t) {
        $safe = '`' . esc_sql($t) . '`';
        $out['vfb_tables'][] = array(
            'table'     => $t,
            'row_count' => (int) $wpdb->get_var('SELECT COUNT(*) FROM ' . $safe),
            'columns'   => wp_list_pluck($wpdb->get_results('SHOW COLUMNS FROM ' . $safe, ARRAY_A), 'Field'),
        );
    }
    return rest_ensure_response($out);
}