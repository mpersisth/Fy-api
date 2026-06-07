package service

func mappedFieldsForMapper(mapper string) []string {
	fields := []string{"prompt", "seconds", "metadata.seed", "metadata.watermark", "metadata.ratio"}
	switch mapper {
	case "seedance_multi_reference_mapper":
		return append(fields, "input_reference", "images", "media")
	case "seedance_storyboard_legacy_mapper":
		return append(fields, "metadata.storyboard", "metadata.shot_count")
	default:
		return fields
	}
}

func droppedFieldsForMapper(mapper string, metadata map[string]interface{}) []string {
	dropped := []string{
		"metadata.fy_quality_strategy",
		"metadata.fy_enhance_force",
		"metadata.fy_enhance_bypass",
	}
	if mapper == "seedance_storyboard_legacy_mapper" {
		for _, key := range []string{"service_tier", "return_last_frame"} {
			if _, ok := metadata[key]; ok {
				dropped = append(dropped, "metadata."+key)
			}
		}
	}
	return dropped
}
